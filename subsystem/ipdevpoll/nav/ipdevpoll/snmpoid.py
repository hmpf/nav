"""Handle scheduling of poll runs according to the NAV snmpoid database."""
__author__ = "Morten Brekkevold (morten.brekkevold@uninett.no)"
__copyright__ = "Copyright 2008 UNINETT AS"
__license__ = "GPLv2"

import logging

from twisted.internet import reactor, defer, task

from nav import ipdevpoll
from nav.ipdevpoll.plugins import plugin_registry

logger = logging.getLogger('nav.ipdevpoll.snmpoid')


class RunHandler(object):

    """Handles a single polling run against a single netbox.

    The responsibility of finding matching plugins and executing them
    in a proper sequence is the responsibility of this class.

    """

    def __init__(self, netbox):
        self.netbox = netbox
        self.logger = ipdevpoll.get_instance_logger(self, 
                                                  "[%s]" % netbox.sysname)

    def find_plugins(self):
        """Populate and sort the interal plugin list."""
        self.plugins = []
        for plugin_class in plugin_registry:
            if plugin_class.can_handle(self.netbox):
                plugin = plugin_class(self.netbox)
                self.plugins.append(plugin)

        if not self.plugins:
            self.logger.warning("No plugins for this run")
            return

        # Sort plugin instances according to their intrinsic
        # comparison methods
        self.plugins.sort()
        self.logger.debug("Plugins to call: %s", 
                          ",".join([p.name() for p in self.plugins]))

    def run(self):
        """Start a polling run against a netbox and retun a deferred."""
        self.logger.info("Starting polling run")
        self.find_plugins()
        self.plugin_iterator = iter(self.plugins)
        self.deferred = defer.Deferred()
        
        # Hop on to the first plugin
        self._nextplugin()
        return self.deferred

    def _nextplugin(self, result=None):
        """Callback that advances to the next plugin in the sequence."""
        try:
            self.current_plugin = self.plugin_iterator.next()
        except StopIteration:
            return self._done()
        else:
            self.logger.debug("Now calling plugin: %s", self.current_plugin)
            df = self.current_plugin.handle()
            # Make sure we advance to next plugin when this one is done
            df.addCallback(self._nextplugin)
            df.addErrback(self._error)

    def _error(self, failure):
        """Error callback that handles plugin failures."""
        if failure.check(ipdevpoll.FatalPluginError):
            # Handle known exceptions from plugins
            self.logger.error("Aborting poll run due to error in plugin "
                              "%s: %s",
                              self.current_plugin, failure.getErrorMessage())
        else:
            # For unknown failures we dump a traceback.  The
            # RunHandler will eat all plugin errors to protect the
            # daemon process.
            self.logger.error("Aborting poll run due to unknown error in "
                              "plugin %s\n%s",
                              self.current_plugin, failure.getTraceback())
        # Release the proxy (i.e. release the listening UDP port so we
        # don't hold on to resources unnecessarily)
        self.netbox.release_proxy()
        #self.deferred.errback(err)
        #return self.deferred

    def _done(self):
        """Performs internal cleanup and callback firing.

        This is called after successful poll run.

        """
        # Release the proxy (i.e. release the listening UDP port so we
        # don't hold on to resources unnecessarily)
        self.netbox.release_proxy()
        self.logger.info("Polling run done")
        # Fire the callback chain
        self.deferred.callback(self)
        return self.deferred


class Schedule(object):

    """Netbox polling schedule handler.

    Does not employ task.LoopingCall because we want to reschedule at
    the end of each RunHandler, not run the handler at fixed times.

    """

    ip_map = {}
    """A map of ip addresses there are currently active RunHandlers for.
    
    Scheduling will not allow simultaineous runs against the same IP
    address, so as to not overload the SNMP agent at that address.

    key: value  -->  str(ip): RunHandler instance
    """
    INTERVAL = 10.0 # seconds


    def __init__(self, netbox):
        self.netbox = netbox
        self.logger = ipdevpoll.get_class_logger(self.__class__)

    def start(self):
        """Start polling schedule."""
        return self._do_poll()

    def _reschedule(self, dummy=None):
        self.delayed = reactor.callLater(self.INTERVAL, self._do_poll)
        self.logger.debug("Rescheduling polling for %s in %s seconds",
                          self.netbox.sysname, self.INTERVAL)
        return dummy

    def _map_cleanup(self, handler):
        """Remove a handler from the ip map."""
        if handler.netbox.ip in Schedule.ip_map:
            del Schedule.ip_map[handler.netbox.ip]
        return handler

    def _do_poll(self, dummy=None):
        ip = self.netbox.ip
        if ip in Schedule.ip_map:
            # We won't start a runhandler now because a runhandler is
            # already polling this IP address.
            other_handler = Schedule.ip_map[ip]
            self.logger.info("schedule clash: waiting for run for %s to "
                             "finish before starting run for %s",
                             other_handler.netbox, self.netbox)
            if id(self.netbox) == id(other_handler.netbox):
                self.logger.debug("Clashing instances are identical")

            # Reschedule this function to be called as soon as the
            # other runhandler is finished
            other_handler.deferred.addCallback(self._do_poll)
        else:
            # We're ok to start a polling run.
            handler = RunHandler(self.netbox)
            Schedule.ip_map[ip] = handler
            deferred = handler.run()
            # Make sure to remove from map and reschedule next run as
            # soon as this one is over.
            deferred.addCallback(self._map_cleanup)
            deferred.addCallback(self._reschedule)
        return dummy


