import time
import logging
from typing import Optional, List, Dict, Any
import pytest
import requests
from requests import exceptions

# Configure logging
logger = logging.getLogger(__name__)

# Global session tracking
_session_start_time: Optional[float] = None
_retry_info: Dict[str, Dict] = {}  # Track retry information


class TelegramConfig:
    """Configuration container for Telegram settings"""
    
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
        """Check if minimum required configuration is present"""
        return bool(self.token and self.chat_id)
    
    @property
    def telegram_api_url(self) -> str:
        """Get Telegram API base URL"""
        return f'https://api.telegram.org/bot{self.token}'


class TestResultsFormatter:
    """Formats test results for Telegram messages"""
    
    def __init__(self, stats: Dict[str, List], session_start_time: float, retry_info: Dict[str, Dict]):
        self.stats = stats
        self.session_start_time = session_start_time
        self.session_duration = time.time() - session_start_time
        self.retry_info = retry_info
        
    @property
    def counts(self) -> Dict[str, int]:
        """Get test result counts"""
        return {
            'passed': len(self.stats.get('passed', [])),
            'failed': len(self.stats.get('failed', [])),
            'skipped': len(self.stats.get('skipped', [])),
            'error': len(self.stats.get('error', []))
        }
    
    @property
    def retry_counts(self) -> Dict[str, int]:
        """Get retry counts"""
        retried_tests = len(self.retry_info)
        total_retries = sum(info['attempts'] - 1 for info in self.retry_info.values())
        return {
            'retried_tests': retried_tests,
            'total_retries': total_retries
        }
    
    @property
    def has_failures(self) -> bool:
        """Check if there are any failures or errors"""
        counts = self.counts
        return counts['failed'] > 0 or counts['error'] > 0
    
    @property
    def has_retries(self) -> bool:
        """Check if there were any retries"""
        return len(self.retry_info) > 0
    
    def format_summary_message(self, env: str, report_url: Optional[str]) -> str:
        """Format the main summary message"""
        counts = self.counts
        retry_counts = self.retry_counts
        
        results_section = (
            f" ‎ 🚀 Passed: *{counts['passed']}*\n"
            f" ☠ Failed: *{counts['failed']}*\n"
            f" 😐 Skipped: *{counts['skipped']}*\n"
            f" 🗿 Errors: *{counts['error']}*\n"
        )
        
        # Add retry information if any retries occurred
        if self.has_retries:
            retry_section = (
                f" 🔄 Retried Tests: *{retry_counts['retried_tests']}*\n"
                f" 🔁 Total Retries: *{retry_counts['total_retries']}*\n"
            )
            results_section += retry_section
        
        timing_section = self._format_timing_section()
        env_section = f"\n‎ ⛺ Environment: *{env}*"
        url_section = f"\n 🤓 Report url: *{report_url}*" if report_url else ""
        
        return f"{results_section}{timing_section}{env_section}{url_section}"
    
    def _format_timing_section(self) -> str:
        """Format timing information"""
        start_time_str = time.strftime(
            "*%d-%m-%Y %H:%M:%S*", 
            time.localtime(self.session_start_time)
        )
        duration_str = time.strftime("*%H:%M:%S*", time.gmtime(self.session_duration))
        
        return (
            f"\n ⌛ Start time: {start_time_str} "
            f"\n ⏰ Time taken: {duration_str}"
        )
    
    def format_failed_tests_message(self) -> Optional[str]:
        """Format failed tests details message including retried failures"""
        failed_tests = self.stats.get('failed', [])
        error_tests = self.stats.get('error', [])
        
        if not failed_tests and not error_tests:
            return None
            
        failed_details = []
        
        # Add regular failed tests
        for test_report in failed_tests:
            message = self._extract_failure_message(test_report)
            # Check if this test had retries
            retry_info = self._get_retry_info_for_test(test_report.nodeid)
            if retry_info:
                message += f" (Failed after {retry_info['attempts']} attempts)"
            failed_details.append(message)
        
        # Add error tests
        for test_report in error_tests:
            message = self._extract_failure_message(test_report)
            # Check if this test had retries
            retry_info = self._get_retry_info_for_test(test_report.nodeid)
            if retry_info:
                message += f" (Error after {retry_info['attempts']} attempts)"
            failed_details.append(message)
            
        return '\n'.join(failed_details)
    
    def format_retry_details_message(self) -> Optional[str]:
        """Format retry details message"""
        if not self.has_retries:
            return None
        
        retry_details = []
        retry_details.append("🔄 *Tests that required retries:*\n")
        
        for test_name, info in self.retry_info.items():
            if info['final_result'] == 'passed':
                status = "✅ Eventually Passed"
            elif info['final_result'] in ['failed', 'error']:
                status = "❌ Still Failed"
            else:
                status = "❓ Unknown Result"
                
            retry_details.append(
                f"• `{test_name}`\n"
                f"  └ Attempts: *{info['attempts']}* - {status}"
            )
        
        return '\n'.join(retry_details)
    
    def _get_retry_info_for_test(self, nodeid: str) -> Optional[Dict]:
        """Get retry information for a specific test"""
        # Try exact match first
        if nodeid in self.retry_info:
            return self.retry_info[nodeid]
        
        # Try to find by test name (in case nodeids don't match exactly)
        test_name = nodeid.split("::")[-1]  # Get just the test function name
        for retry_nodeid, info in self.retry_info.items():
            if test_name in retry_nodeid:
                return info
        
        return None
    
    def _extract_failure_message(self, test_report) -> str:
        """Extract clean failure message from test report"""
        nodeid = test_report.nodeid
        
        if hasattr(test_report.longrepr, 'reprcrash'):
            error_msg = test_report.longrepr.reprcrash.message
        else:
            error_msg = str(test_report.longrepr)
        
        # Clean up the message to remove unwanted details
        clean_message = f"{nodeid} - {error_msg}".split('\n')[0]
        return clean_message


class TelegramNotifier:
    """Handles sending notifications to Telegram"""
    
    def __init__(self, config: TelegramConfig):
        self.config = config
        
    def send_test_results(self, formatter: TestResultsFormatter) -> None:
        """Send test results to Telegram"""
        try:
            # Send sticker if enabled
            message_id = None
            if not self.config.disable_stickers:
                message_id = self._send_sticker(formatter.has_failures)
            
            # Send summary message
            self._send_summary_message(formatter, message_id)
            
            # Send failed tests details if any
            self._send_failed_tests_message(formatter)
            
            # Send retry details if any
            self._send_retry_details_message(formatter)
            
        except exceptions.RequestException as e:
            logger.error("Error sending Telegram message: %s", str(e))
    
    def _send_sticker(self, has_failures: bool) -> Optional[int]:
        """Send appropriate sticker based on test results"""
        sticker_id = (
            self.config.fail_sticker_id if has_failures 
            else self.config.success_sticker_id
        )
        
        payload = {
            'chat_id': self.config.chat_id,
            'sticker': sticker_id
        }
        
        response = self._make_request('/sendSticker', payload)
        return response.json().get('result', {}).get('message_id') if response else None
    
    def _send_summary_message(self, formatter: TestResultsFormatter, reply_to_id: Optional[int]) -> None:
        """Send main summary message"""
        env = self.config.env.replace('\\n', '\n') if self.config.env else ''
        message_text = formatter.format_summary_message(env, self.config.report_url)
        
        payload = {
            'chat_id': self.config.chat_id,
            'text': message_text,
            'parse_mode': 'Markdown'
        }
        
        if reply_to_id:
            payload['reply_to_message_id'] = reply_to_id
            
        response = self._make_request('/sendMessage', payload)
        if response:
            logger.debug("Summary message sent successfully: %s", response.json())
    
    def _send_failed_tests_message(self, formatter: TestResultsFormatter) -> None:
        """Send failed tests details message"""
        failed_message = formatter.format_failed_tests_message()
        if not failed_message:
            return
            
        payload = {
            'chat_id': self.config.chat_id,
            'text': failed_message
        }
        
        response = self._make_request('/sendMessage', payload)
        if response:
            logger.debug("Failed tests message sent successfully: %s", response.json())
    
    def _send_retry_details_message(self, formatter: TestResultsFormatter) -> None:
        """Send retry details message"""
        retry_message = formatter.format_retry_details_message()
        if not retry_message:
            return
            
        payload = {
            'chat_id': self.config.chat_id,
            'text': retry_message,
            'parse_mode': 'Markdown'
        }
        
        response = self._make_request('/sendMessage', payload)
        if response:
            logger.debug("Retry details message sent successfully: %s", response.json())
    
    def _make_request(self, endpoint: str, payload: Dict[str, Any]) -> Optional[requests.Response]:
        """Make HTTP request to Telegram API with error handling"""
        url = f"{self.config.telegram_api_url}{endpoint}"
        
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            return response
        except exceptions.RequestException as e:
            logger.error("Telegram API request failed for %s: %s", endpoint, str(e))
            if 'response' in locals():
                logger.error("Response body: %s", response.text)
            return None


# Plugin configuration
def pytest_addoption(parser):
    """Add command line options for Telegram integration"""
    group = parser.getgroup('telegram')
    
    group.addoption(
        '--telegram_id', 
        action='store', 
        dest='telegram_id', 
        default=None, 
        help='ID of Telegram chat'
    )
    
    group.addoption(
        '--telegram_token', 
        action='store', 
        dest='telegram_token', 
        default=None, 
        help='Telegram bot token'
    )
    
    group.addoption(
        '--telegram_success_sticker_id', 
        action='store', 
        dest='success_sticker_id', 
        default='CAACAgUAAxkBAAErjqJmTc3gMwxZ6lg6xlyvR9mBRFcBiwACBAADIBz8Eom6LgTD9Nq6NQQ', 
        help='File ID of success sticker'
    )
    
    group.addoption(
        '--telegram_fail_sticker_id', 
        action='store', 
        dest='fail_sticker_id', 
        default='CAACAgIAAxkBAAErjqBmTc3YrnVq3X41iPKf_-IByk0bMQACdQEAAonq5Qe1oIsDG4khHDUE', 
        help='File ID of fail sticker'
    )
    
    group.addoption(
        '--telegram_report_url', 
        action='store', 
        dest='telegram_report_url', 
        default=None, 
        help='URL to test report'
    )
    
    group.addoption(
        '--telegram_env', 
        action='store', 
        dest='telegram_env', 
        default=None, 
        help='Environment name'
    )
    
    group.addoption(
        '--telegram_disable_stickers', 
        action='store_true', 
        dest='telegram_disable_stickers', 
        help='Disable sending stickers'
    )


# Plugin hooks
@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session):
    """Capture session start time"""
    global _session_start_time, _retry_info
    _session_start_time = time.time()
    _retry_info = {}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Capture retry information and final test results"""
    global _retry_info
    
    outcome = yield
    report = outcome.get_result()
    
    # Only track call phase (not setup/teardown)
    if call.when == 'call':
        test_nodeid = item.nodeid
        
        # Check for retry-related attributes
        retry_count = None
        
        # Different retry plugins use different attribute names
        for attr_name in ['_pytest_retry_count', 'execution_count', '_retry_count', 'retries']:
            if hasattr(item, attr_name):
                retry_count = getattr(item, attr_name)
                break
        
        # Also check for retry markers
        retry_marker = item.get_closest_marker('flaky') or item.get_closest_marker('retry')
        if retry_marker and not retry_count:
            # Get retry count from marker args
            if retry_marker.args:
                retry_count = retry_marker.args[0] + 1  # +1 because marker is usually max retries
        
        # If we detected retries or this test already has retry info, update it
        if retry_count and retry_count > 1:
            if test_nodeid not in _retry_info:
                _retry_info[test_nodeid] = {
                    'attempts': retry_count,
                    'final_result': report.outcome
                }
            else:
                # Update with the latest information
                _retry_info[test_nodeid]['attempts'] = max(
                    _retry_info[test_nodeid]['attempts'], 
                    retry_count
                )
                _retry_info[test_nodeid]['final_result'] = report.outcome
        
        # Also check if this test was already tracked (from output parsing)
        if test_nodeid in _retry_info:
            _retry_info[test_nodeid]['final_result'] = report.outcome


@pytest.hookimpl(trylast=True)
def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Parse terminal output for retry information and send results"""
    global _retry_info
    
    # Skip if this is a worker process in distributed testing
    if hasattr(terminalreporter.config, 'workerinput'):
        return
    
    # Parse terminal output for additional retry information
    _parse_terminal_output_for_retries(terminalreporter)
    
    # Update retry info with final results from terminalreporter.stats
    _update_retry_final_results(terminalreporter.stats)
    
    # Initialize configuration
    telegram_config = TelegramConfig(config)
    if not telegram_config.is_configured:
        logger.debug("Telegram integration not configured, skipping notification")
        return
    
    # Get session start time with fallback
    session_start_time = (
        _session_start_time or 
        getattr(terminalreporter, '_sessionstarttime', time.time())
    )
    
    # Format results and send notification
    formatter = TestResultsFormatter(terminalreporter.stats, session_start_time, _retry_info)
    notifier = TelegramNotifier(telegram_config)
    notifier.send_test_results(formatter)


def _parse_terminal_output_for_retries(terminalreporter):
    """Parse retry information from pytest terminal output"""
    global _retry_info
    
    # Get the captured output
    output_lines = []
    
    # Try to get output from different sources
    if hasattr(terminalreporter, '_tw') and hasattr(terminalreporter._tw, '_file'):
        if hasattr(terminalreporter._tw._file, 'getvalue'):
            output = terminalreporter._tw._file.getvalue()
            output_lines = output.split('\n')
    
    # Also check if there's captured stdout/stderr
    if hasattr(terminalreporter, 'stats'):
        for section in ['failed', 'error', 'passed']:
            for report in terminalreporter.stats.get(section, []):
                if hasattr(report, 'capstdout'):
                    output_lines.extend(report.capstdout.split('\n'))
                if hasattr(report, 'capstderr'):
                    output_lines.extend(report.capstderr.split('\n'))
    
    # Parse the output for retry patterns
    for line in output_lines:
        line = line.strip()
        
        # Common retry patterns from different plugins
        retry_patterns = [
            ('retrying', 'attempt'),  # "test_name retrying attempt 2"
            ('failed on attempt', 'retrying'),  # "test_name failed on attempt 1! Retrying!"
            ('retry', 'of'),  # "retry 1 of 3"
            ('rerun', 'attempt'),  # "rerun attempt 2"
        ]
        
        for pattern1, pattern2 in retry_patterns:
            if pattern1 in line.lower() and pattern2 in line.lower():
                _extract_retry_info_from_line(line)
                break


def _extract_retry_info_from_line(line: str):
    """Extract retry information from a single output line"""
    global _retry_info
    
    # Try to extract test name and attempt number
    words = line.split()
    
    for i, word in enumerate(words):
        if 'attempt' in word.lower():
            # Look for numbers around 'attempt'
            for j in range(max(0, i-3), min(len(words), i+3)):
                try:
                    attempt_num = int(words[j].strip('!').strip(',').strip('.'))
                    if 1 <= attempt_num <= 10:  # Reasonable attempt number
                        # Try to find test name (usually before 'failed' or 'retrying')
                        test_name = None
                        for k in range(i):
                            if '::' in words[k]:  # Looks like a pytest nodeid
                                test_name = words[k]
                                break
                        
                        if test_name:
                            if test_name not in _retry_info:
                                _retry_info[test_name] = {
                                    'attempts': attempt_num + 1,  # +1 because it will retry
                                    'final_result': 'unknown'
                                }
                            else:
                                _retry_info[test_name]['attempts'] = max(
                                    _retry_info[test_name]['attempts'],
                                    attempt_num + 1
                                )
                        break
                except ValueError:
                    continue


def _update_retry_final_results(stats: Dict[str, List]):
    """Update retry info with final results from test stats"""
    global _retry_info
    
    # Create a mapping of test names to their final results
    test_results = {}
    
    for result_type in ['passed', 'failed', 'error', 'skipped']:
        for report in stats.get(result_type, []):
            test_results[report.nodeid] = result_type
    
    # Update retry info with final results
    for test_name in _retry_info:
        if test_name in test_results:
            _retry_info[test_name]['final_result'] = test_results[test_name]
        else:
            # Try to match by test function name if exact nodeid doesn't match
            test_func_name = test_name.split("::")[-1]
            for nodeid, result in test_results.items():
                if test_func_name in nodeid:
                    _retry_info[test_name]['final_result'] = result
                    break
