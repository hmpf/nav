# encoding: utf-8

import locale
import logging

from nav.logs import init_generic_logging
from nav.logs import reopen_log_files


def test_reopen_log_files_runs_without_error():
    """tests syntax regressions, not actual functionality"""
    assert reopen_log_files() is None


def test_init_generic_logging_file():
    # Get original locale, this might be set by the user running the tests
    orig_locale = locale.getlocale()
    if orig_locale == (None, None):
        orig_locale = None
    else:
        orig_locale = '.'.join(orig_locale)
    # C.UTF-8 is available by default on debian
    locale.setlocale(locale.LC_ALL, 'C.UTF-8')
    tmpfile = '/tmp/test_init_generic_logging_file.txt'
    init_generic_logging(tmpfile, stderr=False)
    logger = logging.getLogger('')
    logger.error('Blåbærsyltetøy')  # Should not raise exception
    locale.setlocale(locale.LC_ALL, 'C')
    logger.error('Blåbærsyltetøy')  # Should not raise exception
    # Set everything back to original locale
    locale.setlocale(locale.LC_ALL, orig_locale)
