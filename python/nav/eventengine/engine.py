#
# Copyright (C) 2012 UNINETT
#
# This file is part of Network Administration Visualized (NAV).
#
# NAV is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License version 2 as published by
# the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.  You should have received a copy of the GNU General Public License
# along with NAV. If not, see <http://www.gnu.org/licenses/>.
#
"""The actual "engine" part of the NAV eventEngine

Will check the eventq ever so often, but will also react to notifications from
PostgreSQL. To add notification of new events posted to eventq, the following
SQL is needed::

    CREATE RULE eventq_notify AS ON INSERT TO eventq DO ALSO NOTIFY new_event;

"""
import logging
import sched
import select
import time
from functools import wraps
import errno
from psycopg2 import OperationalError, InterfaceError
from nav.eventengine.plugin import EventHandler
from nav.eventengine.alerts import AlertGenerator
from nav.eventengine.config import EVENTENGINE_CONF
from nav.eventengine import unresolved
from nav.ipdevpoll.db import commit_on_success
from nav.models.event import EventQueue as Event
from django.db import connection, DatabaseError

_logger = logging.getLogger(__name__)


def retry_on_db_loss(count=3, delay=2):
    """Decorates functions to retry them a set number of times in the face of
    exceptions that appear to be database connection related. If the function
    still fails with database errors after the set number of retries,
    the entire event engine process is aborted.

    :param count: Maximum number of times to retry the function
    :param delay: The number of seconds to sleep between each retry

    """
    def _retry_decorator(func):
        def _retrier(*args, **kwargs):
            remaining = count
            while remaining:
                try:
                    return func(*args, **kwargs)
                except (OperationalError, InterfaceError, DatabaseError):
                    _logger.error("cannot establish db connection. "
                                  "retries remaining: %d", remaining)
                    remaining -= 1
                    time.sleep(delay)
                    continue
            # Die a horrible death if unsuccessful
            _logger.fatal("unable to establish database connection, qutting...")
            raise SystemExit(1)
        return wraps(func)(_retrier)
    return _retry_decorator


def swallow_unhandled_exceptions(func):
    """Decorates a function to log and ignore any exceptions thrown by it
    :param func: The function to decorate
    :return: A decorated version of func

    """

    @wraps(func)
    def _decorated(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            _logger.exception("Unhandled exception occurred; ignoring it")

    return _decorated


class EventEngine(object):
    """Event processing engine.

    Only one instance of this class should ever be needed.

    """
    # interval for regularly scheduled queue checks. these don't need to be
    # too often, since we rely on PostgreSQL notification when new events are
    # inserted into the queue.
    CHECK_INTERVAL = 30
    PLUGIN_TASKS_PRIORITY = 1
    _logger = logging.getLogger(__name__)

    def __init__(self, target="eventEngine", config=EVENTENGINE_CONF):
        self._scheduler = sched.scheduler(time.time, self._notifysleep)
        self.target = target
        self.config = config
        self.last_event_id = 0
        self.handlers = EventHandler.load_and_find_subclasses()
        self._logger.debug("found %d event handler%s: %r",
                           len(self.handlers),
                           's' if len(self.handlers) > 1 else '',
                           self.handlers)

    def _notifysleep(self, delay):
        """Sleeps up to delay number of seconds, but will schedule an
        immediate new event queue check if an event notification is received
        from PostgreSQL.

        """
        conn = connection.connection
        if conn:
            try:
                select.select([conn], [], [], delay)
            except select.error, err:
                if err.args[0] != errno.EINTR:
                    raise
            try:
                conn.poll()
            except OperationalError:
                connection.connection = None
                self._listen()
                return
            if conn.notifies:
                self._logger.debug("got event notification from database")
                self._schedule_next_queuecheck()
                del conn.notifies[:]
        else:
            time.sleep(delay)

    def start(self):
        "Starts the event engine"
        self._logger.info("--- starting event engine ---")
        self._listen()
        self._load_new_events_and_reschedule()
        self._scheduler.run()

    @staticmethod
    @retry_on_db_loss(count=3, delay=5)
    @commit_on_success
    def _listen():
        """Ensures that we subscribe to new_event notifications on our
        PostgreSQL connection.

        """
        _logger.debug("registering event listener with PostgreSQL")
        cursor = connection.cursor()
        cursor.execute('LISTEN new_event')

    def _load_new_events_and_reschedule(self):
        self.load_new_events()
        self._schedule_next_queuecheck(
            self.CHECK_INTERVAL,
            action=self._load_new_events_and_reschedule)

    def _schedule_next_queuecheck(self, delay=0, action=None):
        if not action:
            action = self.load_new_events

        self._scheduler.enter(delay, 0, action, ())

    @swallow_unhandled_exceptions
    @commit_on_success
    def load_new_events(self):
        "Loads and processes new events on the queue, if any"
        self._logger.debug("checking for new events on queue")
        events = Event.objects.filter(target=self.target,
                                      id__gt=self.last_event_id).order_by('id')
        if events:
            events = list(events)
            self._logger.info("found %d new events in queue db", len(events))
            self.last_event_id = events[-1].id
            for event in events:
                unresolved.update()
                try:
                    self.handle_event(event)
                except Exception:
                    self._logger.exception("Unhandled exception while "
                                           "handling %s, deleting event",
                                           event)
                    if event.id:
                        event.delete()

            self._log_task_queue()

    def _log_task_queue(self):
        modified_queue = [
            e for e in self._scheduler.queue
            if e.action != self._load_new_events_and_reschedule
        ]
        if modified_queue:
            self._logger.debug("task queue: %r", modified_queue)

    def _post_generic_alert(self, event):
        alert = AlertGenerator(event)
        if 'alerttype' in event.varmap:
            alert.alert_type = event.varmap['alerttype']

        is_stateless = event.state == Event.STATE_STATELESS
        if is_stateless or not alert.is_event_duplicate():
            self._logger.debug('Posting %s event', event.event_type)
            alert.post()
        else:
            self._logger.info('Ignoring duplicate %s event' % event.event_type)
        event.delete()

    @commit_on_success
    def handle_event(self, event):
        "Handles a single event"
        self._logger.debug("handling %r", event)
        queue = [cls(event, self) for cls in self.handlers
                 if cls.can_handle(event)]
        self._logger.debug("plugins that can handle: %r", queue)
        if not queue:
            self._post_generic_alert(event)

        for handler in queue:
            self._logger.debug("giving event to %s", handler.__class__.__name__)
            try:
                handler.handle()
            except Exception:
                self._logger.exception("Unhandled exception in plugin "
                                       "%s; ignoring it", handler)
                if len(queue) == 1 and event.id:
                    # there's only one handler and it failed,
                    # this will probably never be handled, so we delete it
                    event.delete()

        if event.id:
            self._logger.debug("event wasn't disposed of, "
                               "maybe held for later processing?")

    def schedule(self, delay, action, args=()):
        """Schedule running action after a given delay"""
        return self._scheduler.enter(delay, self.PLUGIN_TASKS_PRIORITY,
                                     swallow_unhandled_exceptions(action),
                                     args)

    def cancel(self, task):
        """Cancel the current scheduled task"""
        self._scheduler.cancel(task)
