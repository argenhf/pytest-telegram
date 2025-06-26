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
    
    def __init__(self, stats: Dict[str, List], session_start_time: float):
        self.stats = stats
        self.session_start_time = session_start_time
        self.session_duration = time.time() - session_start_time
        
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
    def has_failures(self) -> bool:
        """Check if there are any failures or errors"""
        counts = self.counts
        return counts['failed'] > 0 or counts['error'] > 0
    
    def format_summary_message(self, env: str, report_url: Optional[str]) -> str:
        """Format the main summary message"""
        counts = self.counts
        
        results_section = (
            f" ‎ 🚀 Passed: *{counts['passed']}*\n"
            f" ☠ Failed: *{counts['failed']}*\n"
            f" 😐 Skipped: *{counts['skipped']}*\n"
            f" 🗿 Errors: *{counts['error']}*\n"
        )
        
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
        """Format failed tests details message"""
        failed_tests = self.stats.get('failed', [])
        if not failed_tests:
            return None
            
        failed_details = []
        for test_report in failed_tests:
            message = self._extract_failure_message(test_report)
            failed_details.append(message)
            
        return '\n'.join(failed_details)
    
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
    global _session_start_time
    _session_start_time = time.time()


@pytest.hookimpl(hookwrapper=True)
def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Send test results to Telegram after test session completes"""
    yield
    
    # Skip if this is a worker process in distributed testing
    if hasattr(terminalreporter.config, 'workerinput'):
        return
    
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
    formatter = TestResultsFormatter(terminalreporter.stats, session_start_time)
    notifier = TelegramNotifier(telegram_config)
    notifier.send_test_results(formatter)
