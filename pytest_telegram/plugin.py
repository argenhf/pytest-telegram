import time
import logging
import pytest
import requests
from requests import exceptions

logging.getLogger(__name__)

def pytest_addoption(parser):
    group = parser.getgroup('telegram')
    group.addoption('--telegram_id', action='store', dest='telegram_id', default=None, help='ID of Telegram chat')
    group.addoption('--telegram_token', action='store', dest='telegram_token', default=None, help='Telegram token')
    group.addoption('--telegram_success_sticker_id', action='store', dest='success_sticker_id', default='CAACAgUAAxkBAAErjqJmTc3gMwxZ6lg6xlyvR9mBRFcBiwACBAADIBz8Eom6LgTD9Nq6NQQ', help='File ID of success sticker')
    group.addoption('--telegram_fail_sticker_id', action='store', dest='fail_sticker_id', default='CAACAgIAAxkBAAErjqBmTc3YrnVq3X41iPKf_-IByk0bMQACdQEAAonq5Qe1oIsDG4khHDUE', help='File ID of fail sticker')
    group.addoption('--telegram_report_url', action='store', dest='telegram_report_url', default=None, help='Report URL')
    group.addoption('--telegram_env', action='store', dest='telegram_env', default=None, help='Environment')
    group.addoption('--telegram_disable_stickers', action='store_true', dest='telegram_disable_stickers', help='Option to disable stickers')

@pytest.hookimpl(hookwrapper=True)
def pytest_terminal_summary(terminalreporter, exitstatus, config):
    yield

    if not config.option.telegram_token:
        return

    if hasattr(terminalreporter.config, 'workerinput'):
        return

    stats = terminalreporter.stats
    failed_tests = stats.get('failed', [])
    failed_count = len(failed_tests)
    passed_count = len(stats.get('passed', []))
    skipped_count = len(stats.get('skipped', []))
    error_count = len(stats.get('error', []))

    token = config.option.telegram_token
    telegram_uri = f'https://api.telegram.org/bot{token}'
    chat_id = config.option.telegram_id

    success_sticker_id = config.option.success_sticker_id
    fail_sticker_id = config.option.fail_sticker_id
    report_url = f'\n{config.option.telegram_report_url}' if config.option.telegram_report_url else ''
    env = f'{config.option.telegram_env}'.replace('\\n', '\n') if config.option.telegram_env else ''
    disable_stickers = config.option.telegram_disable_stickers

    final_results = f" ‎ 🚀 Passed: *{passed_count}*\n ☠ Failed: *{failed_count}*\n 😐 Skipped: *{skipped_count}*\n 🗿 Errors: *{error_count}*\n"
    session_time = time.time() - terminalreporter._sessionstarttime
    session_start_time = terminalreporter._sessionstarttime
    session_start_time_str = f'\n ⌛ Start time: {time.strftime("*%d-%m-%Y %H:%M:%S*", time.localtime(session_start_time))} '
    time_taken = f'\n ⏰ Time taken: {time.strftime("*%H:%M:%S*", time.gmtime(session_time))}'
    message_text = f'{final_results}{session_start_time_str}{time_taken}\n‎ ⛺ Environment: *{env}*{report_url}'

    try:
        message_id = None
        if not disable_stickers:
            sticker_payload = {'chat_id': chat_id, 'sticker': success_sticker_id if failed_count == 0 and error_count == 0 else fail_sticker_id}
            sticker_response = requests.post(f'{telegram_uri}/sendSticker', json=sticker_payload)
            sticker_response.raise_for_status()
            message_id = sticker_response.json().get('result', {}).get('message_id')

        # Send the summary message
        message_payload = {
            'chat_id': chat_id,
            'text': message_text,
            'reply_to_message_id': message_id,
            'parse_mode': 'Markdown'
        }
        message_response = requests.post(f'{telegram_uri}/sendMessage', json=message_payload)
        message_response.raise_for_status()
        logging.debug("Summary message sent successfully: %s", message_response.json())

        # Send a separate message for failed tests if there are any
        if failed_count > 0:
            failed_tests_details = []
            for test_report in failed_tests:
                nodeid = test_report.nodeid
                if hasattr(test_report.longrepr, 'reprcrash'):
                    message = f'{nodeid} - {test_report.longrepr.reprcrash.message}'
                else:
                    message = f'{nodeid} - {test_report.longrepr}'
                # Clean up the message to remove unwanted details
                clean_message = message.split('\n')[0]
                failed_tests_details.append(clean_message)

            failed_message_payload = {
                'chat_id': chat_id,
                'text': '\n'.join(failed_tests_details)
            }
            failed_message_response = requests.post(f'{telegram_uri}/sendMessage', json=failed_message_payload)
            failed_message_response.raise_for_status()
            logging.debug("Failed tests message sent successfully: %s", failed_message_response.json())

    except exceptions.RequestException as e:
        logging.error("Error sending Telegram message: %s", str(e))
        if 'message_response' in locals():
            logging.error("Telegram response body: %s", message_response.text)
        if 'failed_message_response' in locals():
            logging.error("Telegram response body for failed tests: %s", failed_message_response.text)
