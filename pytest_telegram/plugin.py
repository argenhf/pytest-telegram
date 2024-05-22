import time
import logging
import pytest
import requests
from requests import exceptions

LOG = logging.getLogger(__name__)


def pytest_addoption(parser):
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
        help='Telegram token'
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
        help='Report URL'
    )
    group.addoption(
        '--telegram_custom_text',
        action='store',
        dest='telegram_custom_text',
        default=None,
        help='Custom text to be added to the Telegram message'
    )
    group.addoption(
        '--telegram_disable_stickers',
        action='store_true',
        dest='telegram_disable_stickers',
        help='Option to disable stickers'
    )
    group.addoption(
        '--telegram_list_failed',
        action='store_true',
        dest='telegram_list_failed',
        help='Option to list failed tests'
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_terminal_summary(terminalreporter, exitstatus, config):
    yield

    if not config.option.telegram_token:
        return

    # Special check for pytest-xdist plugin, as we don't want to send a report for each worker
    if hasattr(terminalreporter.config, 'workerinput'):
        return

    stats = terminalreporter.stats
    failed = len(stats.get('failed', []))
    passed = len(stats.get('passed', []))
    skipped = len(stats.get('skipped', []))
    error = len(stats.get('error', []))

    token = config.option.telegram_token
    telegram_uri = f'https://api.telegram.org/bot{token}'
    chat_id = config.option.telegram_id

    success_sticker_id = config.option.success_sticker_id
    fail_sticker_id = config.option.fail_sticker_id
    report_url = f'\n{config.option.telegram_report_url}' if config.option.telegram_report_url else ''
    custom_text = f'\n{config.option.telegram_custom_text}'.replace('\\n',
                                                                    '\n') if config.option.telegram_custom_text else ''
    disable_stickers = config.option.telegram_disable_stickers
    list_failed = config.option.telegram_list_failed
    list_failed_amount = 3

    failed_tests = '\nFailed tests:\n' if list_failed and failed != 0 else ''
    error_tests = '\nError tests:\n' if list_failed and error != 0 else ''

    for failed_test in stats.get('failed', [])[:list_failed_amount]:
        failed_tests += f'{failed_test.nodeid}\n'
    if failed > list_failed_amount:
        failed_tests += '...'

    for error_test in stats.get('error', [])[:list_failed_amount]:
        error_tests += f'{error_test.nodeid}\n'
    if error > list_failed_amount:
        error_tests += '...'

    final_results = '‎ 🚀 Passed: *%s*\n ☠ Failed: *%s*\n 😐 Skipped: *%s*\n 🗿 Errors: *%s*\n' % (
        passed, failed, skipped, error)

    session_time = time.time() - terminalreporter._sessionstarttime
    session_start_time = terminalreporter._sessionstarttime
    session_start_time_str = f'\n ⌛ Start time: {str(time.strftime("*%d-%m-%Y %H:%M:%S*", time.localtime(session_start_time)))} '
    time_taken = f'\n ⏰ Time taken: {str(time.strftime("*%H:%M:%S*", time.gmtime(session_time)))}'

    sticker_payload = {'chat_id': chat_id,
                       'sticker': success_sticker_id if failed == 0 and error == 0 else fail_sticker_id}

    try:
        message_id = None
        if not disable_stickers:
            message_id = requests.post(f'{telegram_uri}/sendSticker', json=sticker_payload).json()['result'][
                'message_id']

        message_payload = {
            'chat_id': chat_id,
            'text': f'{final_results}{session_start_time_str}{time_taken}\n{custom_text}{report_url}\n{failed_tests}{error_tests}',
            'reply_to_message_id': message_id,
            'parse_mode': 'Markdown'
        }
        requests.post(f'{telegram_uri}/sendMessage', json=message_payload).json()
    except exceptions.RequestException as e:
        LOG.error("TELEGRAM Sending Message Error!!!")
        LOG.exception(e)
