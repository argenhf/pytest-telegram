import time
import logging
from typing import Optional, Dict, List, Any
import pytest
import requests
from _pytest.stash import StashKey

logger = logging.getLogger(__name__)

_session_start_time: Optional[float] = None
_retry_info: Dict[str, Dict] = {}
_retry_messages: List[str] = []
retry_key = StashKey[int]()


class TelegramConfig:
    def __init__(self, config):
        self.token = config.option.telegram_token
        self.chat_id = config.option.telegram_id
        self.success_sticker_id = config.option.success_sticker_id
        self.fail_sticker_id = config.option.fail_sticker_id
        self.report_url = config.option.telegram_report_url
        self.env = config.option.telegram_env
        self.disable_stickers = config.option.telegram_disable_stickers

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.chat_id)

    @property
    def telegram_api_url(self) -> str:
        return f'https://api.telegram.org/bot{self.token}'


class TestResultsFormatter:
    def __init__(self, stats: Dict[str, List], session_start_time: float, retry_info: Dict[str, Dict], retry_messages: List[str]):
        self.stats = stats
        self.session_start_time = session_start_time
        self.session_duration = time.time() - session_start_time
        self.retry_info = retry_info
        self.retry_messages = retry_messages

    @property
    def counts(self) -> Dict[str, int]:
        return {
            'passed': len(self.stats.get('passed', [])),
            'failed': len(self.stats.get('failed', [])),
            'skipped': len(self.stats.get('skipped', [])),
            'error': len(self.stats.get('error', []))
        }

    @property
    def has_failures(self) -> bool:
        counts = self.counts
        return counts['failed'] > 0 or counts['error'] > 0

    def format_summary_message(self, env: str, report_url: Optional[str]) -> str:
        counts = self.counts

        results_section = (
            f" ‎ 🚀 Passed: *{counts['passed']}*\n"
            f" ☠ Failed: *{counts['failed']}*\n"
            f" 😐 Skipped: *{counts['skipped']}*\n"
            f" 🗿 Errors: *{counts['error']}*\n"
        )

        start_time_str = time.strftime("*%d-%m-%Y %H:%M:%S*", time.localtime(self.session_start_time))
        duration_str = time.strftime("*%H:%M:%S*", time.gmtime(self.session_duration))
        timing_section = f"\n ⌛ Start time: {start_time_str} \n ⏰ Time taken: {duration_str}"
        env_section = f"\n‎ ⛺ Environment: *{env}*"
        url_section = f"\n 🤓 Report url: *{report_url}*" if report_url else ""

        return f"{results_section}{timing_section}{env_section}{url_section}"

    def format_retry_output_message(self) -> Optional[str]:
        if not self.retry_messages:
            return None

        return (
            "\n\n*The following tests were retried:*\n\n" +
            '\n'.join(self.retry_messages) +
            "\n\n*End of test retry report.*"
        )


class TelegramNotifier:
    def __init__(self, config: TelegramConfig):
        self.config = config

    def send_test_results(self, formatter: TestResultsFormatter) -> None:
        try:
            message_id = None
            if not self.config.disable_stickers:
                message_id = self._send_sticker(formatter.has_failures)

            self._send_summary_message(formatter, message_id)
            self._send_retry_output_message(formatter)

        except requests.RequestException as e:
            logger.error("Error sending Telegram message: %s", str(e))

    def _send_sticker(self, has_failures: bool) -> Optional[int]:
        sticker_id = self.config.fail_sticker_id if has_failures else self.config.success_sticker_id
        payload = {'chat_id': self.config.chat_id, 'sticker': sticker_id}
        response = self._make_request('/sendSticker', payload)
        return response.json().get('result', {}).get('message_id') if response else None

    def _send_summary_message(self, formatter: TestResultsFormatter, reply_to_id: Optional[int]) -> None:
        env = self.config.env.replace('\\n', '\n') if self.config.env else ''
        message_text = formatter.format_summary_message(env, self.config.report_url)
        payload = {
            'chat_id': self.config.chat_id,
            'text': message_text,
            'parse_mode': 'Markdown'
        }
        if reply_to_id:
            payload['reply_to_message_id'] = reply_to_id
        self._make_request('/sendMessage', payload)

    def _send_retry_output_message(self, formatter: TestResultsFormatter) -> None:
        retry_message = formatter.format_retry_output_message()
        if not retry_message:
            return
        payload = {
            'chat_id': self.config.chat_id,
            'text': retry_message,
            'parse_mode': 'Markdown'
        }
        self._make_request('/sendMessage', payload)

    def _make_request(self, endpoint: str, payload: Dict[str, Any]) -> Optional[requests.Response]:
        url = f"{self.config.telegram_api_url}{endpoint}"
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            logger.error("Telegram API request failed: %s", e)
            return None


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session):
    global _session_start_time, _retry_info, _retry_messages
    _session_start_time = time.time()
    _retry_info = {}
    _retry_messages = []


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    current = item.stash.get(retry_key, 0)
    item.stash[retry_key] = current + 1


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    global _retry_info, _retry_messages
    outcome = yield
    report = outcome.get_result()

    if call.when != "call":
        return

    nodeid = item.nodeid
    attempt = item.stash.get(retry_key, 1)

    if report.failed and attempt > 1:
        message = f"    {nodeid} failed on attempt {attempt - 1}! Retrying!\n"
        if hasattr(report.longrepr, 'reprcrash'):
            message += f"        {report.longrepr.reprcrash.message}\n"
        _retry_messages.append(message)

    if report.passed and attempt > 1:
        _retry_messages.append(f"    {nodeid} passed on attempt {attempt}!\n")

    if nodeid not in _retry_info:
        _retry_info[nodeid] = {
            'attempts': attempt,
            'final_result': report.outcome
        }
    else:
        _retry_info[nodeid]['attempts'] = max(_retry_info[nodeid]['attempts'], attempt)
        _retry_info[nodeid]['final_result'] = report.outcome


@pytest.hookimpl(trylast=True)
def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if hasattr(config, 'workerinput'):
        return

    telegram_config = TelegramConfig(config)
    if not telegram_config.is_configured:
        return

    formatter = TestResultsFormatter(
        terminalreporter.stats,
        _session_start_time or time.time(),
        _retry_info,
        _retry_messages
    )
    notifier = TelegramNotifier(telegram_config)
    notifier.send_test_results(formatter)


def pytest_addoption(parser):
    group = parser.getgroup('telegram')
    group.addoption('--telegram_id', action='store', dest='telegram_id', default=None)
    group.addoption('--telegram_token', action='store', dest='telegram_token', default=None)
    group.addoption('--telegram_success_sticker_id', action='store', dest='success_sticker_id', default='CAACAgUAAxkBAAErjqJmTc3gMwxZ6lg6xlyvR9mBRFcBiwACBAADIBz8Eom6LgTD9Nq6NQQ')
    group.addoption('--telegram_fail_sticker_id', action='store', dest='fail_sticker_id', default='CAACAgIAAxkBAAErjqBmTc3YrnVq3X41iPKf_-IByk0bMQACdQEAAonq5Qe1oIsDG4khHDUE')
    group.addoption('--telegram_report_url', action='store', dest='telegram_report_url', default=None)
    group.addoption('--telegram_env', action='store', dest='telegram_env', default=None)
    group.addoption('--telegram_disable_stickers', action='store_true', dest='telegram_disable_stickers')
