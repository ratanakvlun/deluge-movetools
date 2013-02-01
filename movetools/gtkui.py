#
# gtkui.py
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


import gtk
import gtk.glade

from deluge.ui.client import client
from deluge.plugins.pluginbase import GtkPluginBase
import deluge.component as component
from deluge.log import LOG as log

from common import PLUGIN_NAME
from common import MODULE_NAME
from common import DISPLAY_NAME
from common import get_resource


COLUMN_NAME = _("Move Status")


class GtkUI(GtkPluginBase):

  def enable(self):
    log.debug("[%s] Enabling GtkUI...", PLUGIN_NAME)
    self.ui = gtk.glade.XML(get_resource("wnd_preferences.glade"))

    lbl = self.ui.get_widget("lbl_general")
    lbl.set_markup("<b>%s</b>" % lbl.get_text())

    lbl = self.ui.get_widget("lbl_timeout")
    lbl.set_markup("<b>%s</b>" % lbl.get_text())

    component.get("Preferences").add_page(
        DISPLAY_NAME, self.ui.get_widget("blk_preferences"))
    component.get("PluginManager").register_hook(
        "on_apply_prefs", self._do_save_settings)
    component.get("PluginManager").register_hook(
        "on_show_prefs", self._do_load_settings)

    self.menu = self._create_menu()
    self.menu.show_all()

    self.sep = component.get("MenuBar").add_torrentmenu_separator()
    component.get("MenuBar").torrentmenu.append(self.menu)

    self._add_column()

    self._do_load_settings()
    log.debug("[%s] GtkUI enabled", PLUGIN_NAME)

  def disable(self):
    log.debug("[%s] Disabling GtkUI...", PLUGIN_NAME)

    self._remove_column()

    component.get("MenuBar").torrentmenu.remove(self.sep)
    component.get("MenuBar").torrentmenu.remove(self.menu)

    self.menu.destroy()

    component.get("Preferences").remove_page(DISPLAY_NAME)
    component.get("PluginManager").deregister_hook(
        "on_apply_prefs", self._do_save_settings)
    component.get("PluginManager").deregister_hook(
        "on_show_prefs", self._do_load_settings)

    log.debug("[%s] GtkUI disabled", PLUGIN_NAME)

  def _do_save_settings(self):
    log.debug("[%s] Requesting set settings", PLUGIN_NAME)

    config = {
      "general": {
        "remove_empty": self.ui.get_widget("chk_remove_empty").get_active(),
      },
      "timeout": {
        "success": self.ui.get_widget("spn_success_timeout").get_value(),
        "error": self.ui.get_widget("spn_error_timeout").get_value(),
      },
    }

    client.movetools.set_settings(config)

  def _do_load_settings(self):
    log.debug("[%s] Requesting get settings", PLUGIN_NAME)
    client.movetools.get_settings().addCallback(self._do_load)

  def _do_load(self, config):
    chk = self.ui.get_widget("chk_remove_empty")
    chk.set_active(config["general"]["remove_empty"])

    spn = self.ui.get_widget("spn_success_timeout")
    spn.set_value(config["timeout"]["success"])
    spn = self.ui.get_widget("spn_error_timeout")
    spn.set_value(config["timeout"]["error"])

  def _create_menu(self):
    menu = gtk.MenuItem(DISPLAY_NAME)
    submenu = gtk.Menu()

    status_item = gtk.MenuItem(_("Move Status"))
    submenu.append(status_item)

    item = gtk.MenuItem(_("Move Completed"))
    item.connect("activate", self._do_move_completed)
    submenu.append(item)

    status_submenu = gtk.Menu()

    item = gtk.MenuItem(_("Clear"))
    item.connect("activate", self._do_clear_selected)
    status_submenu.append(item)

    item = gtk.MenuItem(_("Clear All"))
    item.connect("activate", self._do_clear_all)
    status_submenu.append(item)

    status_item.set_submenu(status_submenu)
    menu.set_submenu(submenu)

    return menu

  def _do_move_completed(self, widget):
    ids = component.get("TorrentView").get_selected_torrents()
    log.debug("[%s] Requesting move completed for: %s", PLUGIN_NAME, ids)
    client.movetools.move_completed(ids)

  def _do_clear_selected(self, widget):
    ids = component.get("TorrentView").get_selected_torrents()
    log.debug("[%s] Requesting clear status results for: %s",
        PLUGIN_NAME, ids)
    client.movetools.clear_selected(ids)

  def _do_clear_all(self, widget):
    log.debug("[%s] Requesting clear all status results", PLUGIN_NAME)
    client.movetools.clear_all_status()

  def _add_column(self):
    renderer = gtk.CellRendererText()
    renderer.set_padding(5, 0)

    component.get("TorrentView").add_column(
      header=COLUMN_NAME,
      render=renderer,
      col_types=str,
      hidden=False,
      position=None,
      status_field=[MODULE_NAME],
      sortid=0,
      column_type="text",
    )

  def _remove_column(self):
    component.get("TorrentView").remove_column(COLUMN_NAME)
