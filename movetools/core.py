#
# core.py
#
# Copyright (C) 2014 Ratanak Lun <ratanakvlun@gmail.com>
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
import os.path
import copy
import logging

from twisted.internet import reactor

from deluge.plugins.pluginbase import CorePluginBase
import deluge.component as component
import deluge.configmanager
from deluge.core.rpcserver import export
from deluge.core.torrent import Torrent

from common import PLUGIN_NAME
from common import MODULE_NAME


CONFIG_FILE = "%s.conf" % MODULE_NAME

DEFAULT_PREFS = {
  "general": {
    "remove_empty": False,
  },
  "timeout": {
    "success": -1.0,
    "error": -1.0,
  },
}

log = logging.getLogger(__name__)

def get_total_size(paths):
  size = 0
  for path in paths:
    try:
      if os.path.exists(path):
        size += os.path.getsize(path)
    except OSError:
      pass

  return size


class Progress(object):

  def __init__(self, torrent, src, dest):
    files = torrent.get_files()

    src_paths = (os.path.join(src, file["path"]) for file in files)
    self._total = get_total_size(src_paths)

    self._paths = tuple(os.path.join(dest, file["path"]) for file in files)
    self.percent = 0.0

  def update(self):
    size = get_total_size(self._paths)
    self.percent = float(size) / (self._total or 1) * 100


class TorrentMoveJob(object):

  def __init__(self, torrent, dest_path):
    self.torrent = torrent
    self.status = "Moving"
    self.src_path = torrent.get_status(["save_path"])["save_path"]
    self.dest_path = dest_path
    self.progress = Progress(torrent, self.src_path, self.dest_path)


class Core(CorePluginBase):

  def enable(self):

    def move_storage(torrent, dest_path):
      id = str(torrent.handle.info_hash())
      log.debug("[%s] Move storage on: %s", PLUGIN_NAME, id)

      if id in self.torrents:
        if self.torrents[id].status == "Moving":
          log.debug("[%s] Unable to move torrent: already moving", PLUGIN_NAME)
          return False
        else:
          self._remove_job(id)

      self.torrents[id] = TorrentMoveJob(torrent, dest_path)

      if self.torrents[id].src_path == dest_path:
        self.torrents[id].status = "%s: %s" % ("Error", "Same path")
        self._schedule_remove(id, self.timeout["error"])
        return False

      self.queue.append(id)
      return True

    log.debug("[%s] Enabling Core...", PLUGIN_NAME)

    self.initialized = False

    self.config = deluge.configmanager.ConfigManager(CONFIG_FILE,
      copy.deepcopy(DEFAULT_PREFS))

    self.general = self.config["general"]
    self.timeout = self.config["timeout"]

    self.torrents = {}
    self.calls = {}
    self.queue = []
    self.active = None

    component.get("AlertManager").register_handler("storage_moved_alert",
      self.on_storage_moved)
    component.get("AlertManager").register_handler(
      "storage_moved_failed_alert", self.on_storage_moved_failed)

    component.get("CorePluginManager").register_status_field(MODULE_NAME,
      self.get_move_status)

    self.orig_move_storage = Torrent.move_storage
    Torrent.move_storage = move_storage

    self.initialized = True

    log.debug("[%s] Core enabled", PLUGIN_NAME)

    self._update_loop()

  def disable(self):
    log.debug("[%s] Disabling Core...", PLUGIN_NAME)

    self.initialized = False

    Torrent.move_storage = self.orig_move_storage

    for id in self.torrents:
      self._cancel_remove(id)

    component.get("CorePluginManager").deregister_status_field(MODULE_NAME)

    component.get("AlertManager").deregister_handler(
        self.on_storage_moved)
    component.get("AlertManager").deregister_handler(
        self.on_storage_moved_failed)

    deluge.configmanager.close(CONFIG_FILE)

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
      if id in self.torrents and self.torrents[id].status != "Moving":
        self._remove_job(id)

  @export
  def clear_all_status(self):
    log.debug("[%s] Clearing all status results", PLUGIN_NAME)
    for id in self.torrents.keys():
      if self.torrents[id].status != "Moving":
        self._remove_job(id)

  @export
  def move_completed(self, ids):
    log.debug("[%s] Moving completed torrents in: %s", PLUGIN_NAME, ids)
    torrents = component.get("TorrentManager").torrents
    for id in ids:
      if id in torrents:
        torrent = torrents[id]
        if torrent.handle.is_finished():
          dest_path = torrent.options["move_completed_path"]
          if not dest_path or not torrent.move_storage(dest_path):
            log.error("[%s] Could not move storage: General failure: %s",
              PLUGIN_NAME, id)

  def on_storage_moved(self, alert):
    id = str(alert.handle.info_hash())
    if id in self.torrents:
      self.active = None
      self.torrents[id].status = "Done"
      self._schedule_remove(id, self.timeout["success"])

      if self.general["remove_empty"]:
        try:
          log.debug("[%s] Removing empty folders in path: %s", PLUGIN_NAME,
            self.torrents[id].src_path)
          os.removedirs(self.torrents[id].src_path)
        except OSError:
          pass

  def on_storage_moved_failed(self, alert):
    id = str(alert.handle.info_hash())
    if id in self.torrents:
      self.active = None
      message = alert.message().rpartition(":")[2].strip()
      log.debug("[%s] Error: %s", PLUGIN_NAME, message)

      self.torrents[id].status = "%s: %s" % ("Error", message)
      self._schedule_remove(id, self.timeout["error"])

  def get_move_status(self, id):
    if id not in self.torrents:
      return ""

    status = self.torrents[id].status

    if status == "Moving":
      percent = self.torrents[id].progress.percent
      if percent < 100.0:
        percent_str = "%.2f" % percent
      else:
        percent_str = "99.99"

      status = "Moving %s" % percent_str

    return status

  def _update_loop(self):

    if not self.initialized:
      return

    if self.active is None:
      while self.queue:
        id = self.queue.pop(0)
        if id in self.torrents:
          job = self.torrents[id]
          if self.orig_move_storage(job.torrent, job.dest_path):
            self.active = id
            break

          self.torrents[self.active].status = "%s: %s" % ("Error",
            "General failure")
          self._schedule_remove(self.active, self.timeout["error"])

        self.active = None

    if self.active:
      self.torrents[self.active].progress.update()

    reactor.callLater(1.0, self._update_loop)

  def _remove_job(self, id):
    self._cancel_remove(id)

    if id in self.queue:
      self.queue.remove(id)

    if id in self.torrents:
      del self.torrents[id]

  def _schedule_remove(self, id, time):
    self._cancel_remove(id)
    if time >= 0:
      self.calls[id] = reactor.callLater(time, self._remove_job, id)

  def _cancel_remove(self, id):
    if id in self.calls:
      if self.calls[id].active():
        self.calls[id].cancel()
      del self.calls[id]

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
