#
# core.py
#
# Copyright (C) 2013 Ratanak Lun <ratanakvlun@gmail.com>
#
# Basic plugin template created by:
# Copyright (C) 2008 Martijn Voncken <mvoncken@gmail.com>
# Copyright (C) 2007-2009 Andrew Resch <andrewresch@gmail.com>
# Copyright (C) 2009 Damien Churchill <damoxc@gmail.com>
#
# Deluge is free software.
#
# You may redistribute it and/or modify it under the terms of the
# GNU General Public License, as published by the Free Software
# Foundation; either version 3 of the License, or (at your option)
# any later version.
#
# deluge is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with deluge.    If not, write to:
#   The Free Software Foundation, Inc.,
#   51 Franklin Street, Fifth Floor
#   Boston, MA  02110-1301, USA.
#
#    In addition, as a special exception, the copyright holders give
#    permission to link the code of portions of this program with the OpenSSL
#    library.
#    You must obey the GNU General Public License in all respects for all of
#    the code used other than OpenSSL. If you modify file(s) with this
#    exception, you may extend this exception to your version of the file(s),
#    but you are not obligated to do so. If you do not wish to do so, delete
#    this exception statement from your version. If you delete this exception
#    statement from all source files in the program, then also delete it here.
#


import os

from twisted.internet import reactor

from deluge.plugins.pluginbase import CorePluginBase
import deluge.component as component
import deluge.configmanager
from deluge.core.rpcserver import export
from deluge.core.torrent import Torrent
from deluge.log import LOG as log

from common import PLUGIN_NAME
from common import MODULE_NAME


DEFAULT_PREFS = {
  "general": {
    "remove_empty": False,
  },
  "timeout": {
    "success": -1.0,
    "error": -1.0,
  },
}


class Core(CorePluginBase):

  def enable(self):
    log.debug("[%s] Enabling Core...", PLUGIN_NAME)
    self.config = deluge.configmanager.ConfigManager(
        "%s.conf" % MODULE_NAME, DEFAULT_PREFS)

    self.general = self.config["general"]
    self.timeout = self.config["timeout"]

    self.status = {}
    self.deferred = {}
    self.paths = {}

    component.get("AlertManager").register_handler(
        "storage_moved_alert", self.on_storage_moved)
    component.get("AlertManager").register_handler(
        "storage_moved_failed_alert", self.on_storage_moved_failed)

    component.get("CorePluginManager").register_status_field(
        MODULE_NAME, self._get_move_status)

    def wrapper(obj, dest):
      id = str(obj.handle.info_hash())
      log.debug("[%s] (Wrapped) Move storage on: %s", PLUGIN_NAME, id)
      self._cancel_deferred(id)

      old_path = obj.get_status(["save_path"])["save_path"]
      if old_path == dest:
        self.status[id] = "%s: %s" % (_("Error"), _("Same path"))
        self._clear_move_status(id, self.timeout["error"])
        return False

      _orig_move_storage = self.orig_move_storage
      result = _orig_move_storage(obj, dest)
      if result:
        self.status[id] = _("Moving")

        if self.general["remove_empty"]:
          self.paths[id] = old_path
      else:
        self.status[id] = "%s: %s" % (_("Error"), _("General failure"))
        self._clear_move_status(id, self.timeout["error"])

      return result

    self.orig_move_storage = Torrent.move_storage
    Torrent.move_storage = wrapper
    log.debug("[%s] Core enabled", PLUGIN_NAME)

  def disable(self):
    log.debug("[%s] Disabling Core...", PLUGIN_NAME)
    Torrent.move_storage = self.orig_move_storage

    for id in self.deferred.keys():
      self._cancel_deferred(id)

    component.get("CorePluginManager").deregister_status_field(MODULE_NAME)

    component.get("AlertManager").deregister_handler(
        self.on_storage_moved)
    component.get("AlertManager").deregister_handler(
        self.on_storage_moved_failed)

    deluge.configmanager.close(self.config)

    self._rpc_deregister(PLUGIN_NAME)
    log.debug("[%s] Core disabled", PLUGIN_NAME)

  @export
  def set_settings(self, options):
    log.debug("[%s] Setting options", PLUGIN_NAME)
    self.general.update(options["general"])
    self.timeout.update(options["timeout"])
    self.config.save()

  @export
  def get_settings(self):
    log.debug("[%s] Getting options", PLUGIN_NAME)
    return {
      "general": self.general,
      "timeout": self.timeout,
    }

  @export
  def clear_selected(self, ids):
    log.debug("[%s] Clearing status results for: %s", PLUGIN_NAME, ids)
    for id in ids:
      if id in self.status and self.status[id] != _("Moving"):
        self._cancel_deferred(id)
        self._clear_move_status(id)

  @export
  def clear_all_status(self):
    log.debug("[%s] Clearing all status results", PLUGIN_NAME)
    for id in self.status.keys():
      if self.status[id] != _("Moving"):
        self._cancel_deferred(id)
        self._clear_move_status(id)

  @export
  def move_completed(self, ids):
    log.debug("[%s] Moving completed torrents in: %s", PLUGIN_NAME, ids)
    torrents = component.get("TorrentManager").torrents
    for id in ids:
      if id in torrents:
        torrent = torrents[id]
        if torrent.handle.is_finished():
          dest = torrent.options["move_completed_path"]
          if not dest:
            self._cancel_deferred(id)
            self.status[id] = "%s: %s" % (_("Error"), _("Pathname is empty"))
            self._clear_move_status(id, self.timeout["error"])
          elif not torrent.move_storage(dest):
            log.error("[%s] Could not move storage: %s", PLUGIN_NAME, id)

  def on_storage_moved(self, alert):
    id = str(alert.handle.info_hash())

    if id in self.paths:
      if self.general["remove_empty"]:
        try:
          log.debug("[%s] Removing empty folders in path: %s",
              PLUGIN_NAME, self.paths[id])
          os.removedirs(self.paths[id])
        except OSError as e:
          pass

      del self.paths[id]

    if id in self.status:
      self._cancel_deferred(id)
      self.status[id] = _("Done")
      self._clear_move_status(id, self.timeout["success"])

  def on_storage_moved_failed(self, alert):
    id = str(alert.handle.info_hash())

    if id in self.paths:
      del self.paths[id]

    if id in self.status:
      self._cancel_deferred(id)
      message = alert.message().rpartition(":")[2].strip()
      self.status[id] = "%s: %s" % (_("Error"), _(message))
      self._clear_move_status(id, self.timeout["error"])
      log.debug("[%s] Error: %s", PLUGIN_NAME, message)

  def _get_move_status(self, id):
    return self.status.get(id) or ""

  def _clear_move_status(self, id, secs=0):
    if secs > 0:
      self._cancel_deferred(id)
      self.deferred[id] = reactor.callLater(secs, self._clear_move_status, id)
    elif secs == 0:
      if id in self.status:
        if id in self.deferred:
          del self.deferred[id]
        del self.status[id]

  def _cancel_deferred(self, id):
    if id in self.deferred:
      self.deferred[id].cancel()
      del self.deferred[id]

  def _rpc_deregister(self, name):
    server = component.get("RPCServer")
    name = name.lower()

    for d in dir(self):
      if d[0] == "_": continue

      if getattr(getattr(self, d), '_rpcserver_export', False):
        method = "%s.%s" % (name, d)
        if method in server.factory.methods:
          log.debug("Deregistering method: %s", method)
          del server.factory.methods[method]
