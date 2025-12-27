#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

import os.path

import gi
gi.require_version('Adw', '1')
from gi.repository import Adw

from lemma.services.color_manager import ColorManager
from lemma.services.message_bus import MessageBus
from lemma.services.paths import Paths
from lemma.services.settings import Settings
import lemma.services.timer as timer


class Colors(object):

    def __init__(self, main_window):
        self.main_window = main_window

        self.color_scheme = None
        self.dark_mode = None
        self.style_manager = Adw.StyleManager.get_default()
        if self.style_manager is not None:
            self.style_manager.connect('notify::dark', self.on_style_manager_changed)
            self.style_manager.connect('notify::color-scheme', self.on_style_manager_changed)

        MessageBus.subscribe(self, 'settings_changed')

        self.update()

    @timer.timer
    def animate(self):
        messages = MessageBus.get_messages(self)
        if 'settings_changed' in messages:
            self.update()

    @timer.timer
    def update(self):
        color_scheme = Settings.get_value('color_scheme')
        dark_mode = self.style_manager.get_dark() if self.style_manager is not None else False

        if color_scheme == self.color_scheme and dark_mode == self.dark_mode: return

        self.color_scheme = Settings.get_value('color_scheme')
        self.dark_mode = dark_mode
        if self.color_scheme == 'default':
            theme_name = 'default-dark.css' if self.dark_mode else 'default.css'
            path = os.path.join(Paths.get_resources_folder(), 'themes', theme_name)
        else:
            path = Settings.get_value('color_scheme')

        self.main_window.css_provider_colors.load_from_path(path)
        self.main_window.main_box.queue_draw()
        self.main_window.document_view.content.queue_draw()
        self.main_window.document_list.content.queue_draw()

        ColorManager.invalidate_cache()

    def on_style_manager_changed(self, *args):
        self.update()

