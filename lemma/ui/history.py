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

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GObject, Gtk, Pango, PangoCairo

import time

from lemma.services.message_bus import MessageBus
from lemma.services.color_manager import ColorManager
from lemma.application_state.application_state import ApplicationState
from lemma.use_cases.use_cases import UseCases
from lemma.repos.workspace_repo import WorkspaceRepo
from lemma.repos.document_repo import DocumentRepo
from lemma.ui.helpers.cairo import rounded_rectangle
import lemma.services.timer as timer


class History(object):

    def __init__(self, main_window):
        self.view = main_window.history_view

        self.layout = Pango.Layout(self.view.get_pango_context())
        self.layout.set_ellipsize(Pango.EllipsizeMode.END)
        self.layout.set_alignment(Pango.Alignment.CENTER)
        self.font_desc_normal = self.layout.get_context().get_font_description()
        self.font_desc_bold = self.layout.get_context().get_font_description()
        self.font_desc_bold.set_weight(Pango.Weight.BOLD)

        self.items = list()
        self.active_document_index = None
        self.selected_index = None
        self.dragging_document_id = None
        self.drag_history_override = None
        self.drag_start_x = None
        self.drag_target_index = None
        self.dragging_offset_override = None
        self.animated_offsets = dict()
        self.target_offsets = dict()
        self.history_items_data = list()
        self.history_widths = dict()
        self.total_width = 0
        self.animation_timeout_id = None
        self.hover_lock_until = None

        self.size_cache = dict()

        self.view.content.set_draw_func(self.draw)
        self.view.scrolling_widget.connect('primary_button_press', self.on_primary_button_press)
        self.view.scrolling_widget.connect('primary_button_release', self.on_primary_button_release)
        self.drag_controller = Gtk.GestureDrag()
        self.drag_controller.set_button(1)
        self.drag_controller.connect('drag-begin', self.on_drag_begin)
        self.drag_controller.connect('drag-update', self.on_drag_update)
        self.drag_controller.connect('drag-end', self.on_drag_end)
        self.view.content.add_controller(self.drag_controller)

        MessageBus.subscribe(self, 'history_changed')
        MessageBus.subscribe(self, 'document_title_changed')
        MessageBus.subscribe(self, 'mode_set')

        self.update()

    @timer.timer
    def animate(self):
        messages = MessageBus.get_messages(self)
        if 'history_changed' in messages or 'document_title_changed' in messages or 'mode_set' in messages:
            self.update()

    @timer.timer
    def update(self):
        self.update_size()
        self.scroll_active_document_on_screen()
        self.view.content.queue_draw()

    @timer.timer
    def update_size(self):
        workspace = WorkspaceRepo.get_workspace()
        history_ids = self.drag_history_override if self.drag_history_override != None else workspace.get_history()
        history = [DocumentRepo.get_stub_by_id(doc_id) for doc_id in history_ids]
        mode = workspace.get_mode()

        total_width = 0
        self.items = list()
        self.active_document_index = None
        self.history_items_data = list()
        self.history_widths = dict()
        target_offsets = dict()

        for i, document_stub in enumerate(history):
            if document_stub['title'] not in self.size_cache:
                self.size_cache[document_stub['title']] = self.get_item_extents(document_stub['title']).width / Pango.SCALE + 37
            document_width = self.size_cache[document_stub['title']]
            target_offsets[document_stub['id']] = total_width
            self.history_widths[document_stub['id']] = document_width
            self.history_items_data.append((i, document_stub, document_width))
            total_width += document_width
            if document_stub['id'] == workspace.get_active_document_id():
                self.active_document_index = i
                if mode == 'draft':
                    break
        if mode == 'draft':
            total_width += self.get_item_extents('New Document').width / Pango.SCALE + 37
        total_width += 72

        self.total_width = total_width
        self.view.scrolling_widget.set_size(total_width, 1)
        self.target_offsets = target_offsets
        self.ensure_offsets_initialized()
        self.update_items_from_offsets()
        self.schedule_offset_animation()

    @timer.timer
    def scroll_active_document_on_screen(self):
        if self.view.scrolling_widget.adjustment_x.get_upper() < self.view.scrolling_widget.scrolling_offset_x + self.view.scrolling_widget.width or self.active_document_index == len(self.items) - 1:
            self.view.scrolling_widget.scroll_to_position((self.view.scrolling_widget.adjustment_x.get_upper(), 0))
            return

        if self.active_document_index != None:
            i, document_stub, document_offset, document_width = self.items[self.active_document_index]
            if document_offset < self.view.scrolling_widget.scrolling_offset_x:
                self.view.scrolling_widget.scroll_to_position((document_offset, 0))
                return

            i, document_stub, document_offset, document_width = self.items[self.active_document_index + 1]
            if document_offset > self.view.scrolling_widget.scrolling_offset_x + self.view.scrolling_widget.width:
                self.view.scrolling_widget.scroll_to_position((document_offset - self.view.scrolling_widget.width - 1, 0))
                return

    def on_primary_button_press(self, scrolling_widget, data):
        x_offset, y_offset, state = data

        if state == 0:
            hover_index = self.get_hover_index()
            if hover_index != None:
                self.set_selected_index(hover_index)

    def on_primary_button_release(self, scrolling_widget, data):
        x_offset, y_offset, state = data

        if self.dragging_document_id != None:
            self.set_selected_index(None)
            return

        hover_index = self.get_hover_index()
        if hover_index != None and hover_index == self.selected_index:
            UseCases.set_active_document(self.items[hover_index][1]['id'], update_history=False)
        self.set_selected_index(None)

    def set_selected_index(self, index):
        if index != self.selected_index:
            self.selected_index = index
            self.view.content.queue_draw()

    @timer.timer
    def draw(self, widget, ctx, width, height):
        workspace = WorkspaceRepo.get_workspace()
        mode = workspace.get_mode()

        now = time.time()
        hover_index = self.get_dragging_index() if self.dragging_document_id != None else self.get_hover_index()
        if self.hover_lock_until != None and now < self.hover_lock_until:
            hover_index = self.selected_index
        scrolling_offset = int(self.view.scrolling_widget.scrolling_offset_x) + 1
        hover_color = ColorManager.get_ui_color('history_hover')
        selected_color = ColorManager.get_ui_color('history_active_bg')
        fg_color = ColorManager.get_ui_color('history_fg')

        draft_offset = 0
        selected_index_for_draw = self.get_dragging_index() if self.dragging_document_id != None else self.selected_index

        if self.active_document_index != None or mode != 'draft':
            for i, document_stub, document_offset, document_width in self.items:
                is_active = (i == self.active_document_index)
                if document_offset + document_width >= self.view.scrolling_widget.scrolling_offset_x and document_offset <= self.view.scrolling_widget.scrolling_offset_x + width:
                    font_desc = self.font_desc_bold if (is_active and mode != 'draft') else self.font_desc_normal

                    if i == hover_index:
                        if i == selected_index_for_draw:
                            Gdk.cairo_set_source_rgba(ctx, selected_color)
                        else:
                            Gdk.cairo_set_source_rgba(ctx, hover_color)
                        rounded_rectangle(ctx, document_offset - scrolling_offset, 6, document_width, 35, 6)
                        ctx.fill()

                    ctx.move_to(document_offset - scrolling_offset, 13)
                    self.layout.set_font_description(font_desc)
                    self.layout.set_width(document_width * Pango.SCALE)
                    self.layout.set_text(str(document_stub['title']))
                    Gdk.cairo_set_source_rgba(ctx, fg_color)
                    PangoCairo.show_layout(ctx, self.layout)
                    self.draw_divider(ctx, document_offset - scrolling_offset, height)

                if is_active and mode == 'draft':
                    draft_offset = document_offset + document_width + 1 - scrolling_offset
                    break

        if mode == 'draft':
            extents = self.get_item_extents('New Document')
            ctx.move_to(draft_offset, 13)
            self.layout.set_font_description(self.font_desc_bold)
            self.layout.set_width(extents.width + 37 * Pango.SCALE)
            self.layout.set_text('New Document')
            Gdk.cairo_set_source_rgba(ctx, fg_color)
            PangoCairo.show_layout(ctx, self.layout)

            if draft_offset > 0:
                self.draw_divider(ctx, draft_offset, height)

    def draw_divider(self, ctx, offset, height):
        Gdk.cairo_set_source_rgba(ctx, ColorManager.get_ui_color('border_1'))
        ctx.rectangle(offset, 9, 1, height - 18)
        ctx.fill()

    def get_hover_index(self):
        y = self.view.scrolling_widget.cursor_y
        x = self.view.scrolling_widget.cursor_x
        if y == None or x == None: return None
        if y < 6 or y > 41: return None
        x += self.view.scrolling_widget.scrolling_offset_x

        offset = 0
        for i, document_stub, document_offset, document_width in self.items:
            if x >= document_offset and x < document_offset + document_width:
                return i
        return None

    def on_drag_begin(self, gesture, x, y):
        hover_index = self.get_hover_index()
        if hover_index == None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        self.dragging_document_id = self.items[hover_index][1]['id']
        self.drag_history_override = WorkspaceRepo.get_workspace().get_history()[:]
        self.drag_start_x = self.view.scrolling_widget.scrolling_offset_x + x
        self.drag_target_index = hover_index
        self.dragging_offset_override = self.get_dragging_offset_for_position(self.drag_start_x)
        self.set_selected_index(hover_index)
        self.update_items_from_offsets()
        self.view.content.queue_draw()
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def on_drag_update(self, gesture, offset_x, offset_y):
        if self.dragging_document_id == None:
            return

        current_x = self.drag_start_x + offset_x
        self.dragging_offset_override = self.get_dragging_offset_for_position(current_x)
        target_index = self.get_target_index()

        if target_index == None:
            self.update_items_from_offsets()
            self.view.content.queue_draw()
            return

        new_history = self.drag_history_override[:]
        new_history.remove(self.dragging_document_id)
        new_history.insert(target_index, self.dragging_document_id)

        if new_history != self.drag_history_override:
            self.drag_history_override = new_history
            self.drag_target_index = target_index
            self.set_selected_index(self.drag_history_override.index(self.dragging_document_id))
            self.update()
        else:
            self.update_items_from_offsets()
            self.view.content.queue_draw()

    def on_drag_end(self, gesture, offset_x, offset_y):
        if self.dragging_document_id == None:
            return

        final_index = None
        if self.drag_history_override != None and self.dragging_document_id in self.drag_history_override:
            final_index = self.drag_history_override.index(self.dragging_document_id)

        self.drag_history_override = None
        self.drag_start_x = None
        self.drag_target_index = None
        self.dragging_offset_override = None
        dragging_document_id = self.dragging_document_id
        self.dragging_document_id = None

        if final_index != None:
            UseCases.move_history_item(dragging_document_id, final_index)
            self.set_selected_index(final_index)
            self.hover_lock_until = time.time() + 0.25
        self.update()

    def get_target_index(self):
        if len(self.items) == 0:
            return 0

        if self.dragging_document_id == None or self.dragging_offset_override == None:
            return None

        width = self.history_widths.get(self.dragging_document_id, 0)
        center = self.dragging_offset_override + width / 2

        boundary_index = 0
        running_offset = 0
        for i, document_stub, document_width in self.history_items_data:
            if document_stub['id'] == self.dragging_document_id:
                continue

            boundary = running_offset + document_width / 2
            if center < boundary:
                return boundary_index

            running_offset += document_width
            boundary_index += 1

        return boundary_index

    def get_dragging_index(self):
        if self.dragging_document_id == None:
            return None
        for i, document_stub, document_offset, document_width in self.items:
            if document_stub['id'] == self.dragging_document_id:
                return i
        return None

    def get_dragging_offset_for_position(self, current_x):
        if self.dragging_document_id == None:
            return 0

        width = self.history_widths.get(self.dragging_document_id, 0)
        offset = current_x - width / 2
        offset = max(0, min(offset, max(0, self.total_width - width)))
        return offset

    def ensure_offsets_initialized(self):
        for doc_id, target_offset in self.target_offsets.items():
            if doc_id not in self.animated_offsets:
                self.animated_offsets[doc_id] = target_offset

        for doc_id in list(self.animated_offsets.keys()):
            if doc_id not in self.target_offsets:
                del(self.animated_offsets[doc_id])

        if self.dragging_document_id != None and self.dragging_document_id in self.target_offsets and self.dragging_offset_override != None:
            self.animated_offsets[self.dragging_document_id] = self.dragging_offset_override

    def update_items_from_offsets(self):
        self.items = list()

        for i, document_stub, document_width in self.history_items_data:
            offset = self.animated_offsets.get(document_stub['id'], self.target_offsets.get(document_stub['id'], 0))
            self.items.append((i, document_stub, offset, document_width))

    def schedule_offset_animation(self):
        if self.animation_timeout_id == None:
            self.animation_timeout_id = GObject.timeout_add(15, self.animate_offsets)

    def animate_offsets(self):
        if len(self.target_offsets.keys()) == 0:
            self.animation_timeout_id = None
            return False

        updated = False
        for doc_id, target_offset in self.target_offsets.items():
            if doc_id == self.dragging_document_id and self.dragging_offset_override != None:
                new_offset = self.dragging_offset_override
            else:
                current_offset = self.animated_offsets.get(doc_id, target_offset)
                difference = target_offset - current_offset

                if abs(difference) < 0.5:
                    new_offset = target_offset
                else:
                    new_offset = current_offset + difference * 0.2

            if abs(new_offset - target_offset) > 0.01:
                updated = True
            self.animated_offsets[doc_id] = new_offset

        self.update_items_from_offsets()
        self.view.content.queue_draw()

        if updated:
            return True
        else:
            self.animation_timeout_id = None
            return False

    @timer.timer
    def get_item_extents(self, text):
        self.layout.set_font_description(self.font_desc_bold)
        self.layout.set_width(-1)
        self.layout.set_text(text)
        return self.layout.get_extents()[1]
