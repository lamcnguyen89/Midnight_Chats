"""Publish an optional .local hostname using the Thor's existing Avahi daemon."""

import ipaddress
import logging
import shutil
import socket
import subprocess
import threading

logger = logging.getLogger(__name__)


def lan_address(host, explicit_address=""):
    if explicit_address:
        address = explicit_address
    else:
        try:
            candidate = ipaddress.IPv4Address(host)
        except ipaddress.AddressValueError:
            candidate = None
        if candidate is not None and not candidate.is_unspecified:
            address = str(candidate)
        else:
            # UDP connect selects the default route without transmitting any data.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                route.connect(("192.0.2.1", 9))
                address = route.getsockname()[0]
    try:
        parsed = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        raise ValueError("MIDNIGHT_ADDRESS must be the Thor's LAN IPv4 address.") from None
    if parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast or str(parsed) == "255.255.255.255":
        raise ValueError("MIDNIGHT_ADDRESS must be a reachable LAN IPv4 address.")
    return str(parsed)


class AliasPublisher:
    refresh_interval = 5

    def __init__(self, settings):
        self.settings = settings
        self.process = None
        self.address = None
        self._stopped = threading.Event()
        self._thread = None

    def start(self):
        if not self.settings.alias or self._thread is not None:
            return
        executable = shutil.which("avahi-publish-address")
        if not executable:
            logger.warning("MIDNIGHT_ALIAS requires avahi-utils and a running avahi-daemon. Use the LAN IP until Avahi is available.")
            return
        try:
            self._refresh(executable)
        except OSError:
            # Wi-Fi and Avahi can still be starting at boot. Retry without
            # taking down the HTTP server or permanently losing the alias.
            logger.warning("alias_publication_deferred", exc_info=True)
        except Exception:
            logger.exception("alias_publication_failed", extra={"fields": {"alias": self.settings.alias}})
            raise
        self._stopped.clear()
        self._thread = threading.Thread(target=self._monitor, args=(executable,),
                                        name="alias-publisher", daemon=True)
        self._thread.start()

    def _refresh(self, executable):
        address = lan_address(self.settings.host, self.settings.address)
        if self.process is not None and self.process.poll() is None and address == self.address:
            return
        self._stop_process()
        self.process = subprocess.Popen(
            [executable, "--no-reverse", self.settings.alias, address], stdout=subprocess.DEVNULL,
        )
        self.address = address
        logger.info("alias_publisher_started", extra={"fields": {
            "alias": self.settings.alias, "address": address, "port": self.settings.port,
            "publisher_pid": self.process.pid}})

    def _monitor(self, executable):
        while not self._stopped.wait(self.refresh_interval):
            try:
                self._refresh(executable)
            except Exception:
                logger.exception("alias_publication_retry_failed", extra={"fields": {"alias": self.settings.alias}})

    def stop(self):
        self._stopped.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self._stop_process()

    def _stop_process(self):
        if self.process is None:
            return
        return_code = self.process.poll()
        if return_code is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("alias_publisher_forced_shutdown", extra={"fields": {"alias": self.settings.alias}})
                self.process.kill()
                self.process.wait(timeout=5)
        elif return_code != 0:
            logger.error("alias_publisher_exited_unexpectedly", extra={"fields": {
                "alias": self.settings.alias, "return_code": return_code}})
        logger.info("alias_publisher_stopped", extra={"fields": {"alias": self.settings.alias}})
        self.process = None
        self.address = None
