# -*- coding: utf-8 -*-

# Copyright (C) 2011 ~ 2012 Deepin, Inc.
#               2011 ~ 2012 Wang Yong
# 
# Author:     Wang Yong <lazycat.manatee@gmail.com>
# Maintainer: Wang Yong <lazycat.manatee@gmail.com>
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
from skin import app_theme
from nls import _

import glib
from data import data_exit
from icon_window import IconWindow
from detail_page import DetailPage
from dtk.ui.menu import Menu
from dtk.ui.constant import WIDGET_POS_BOTTOM_LEFT
from dtk.ui.button import LinkButton
from dtk.ui.slider import Wizard
from dtk.ui.navigatebar import Navigatebar
from dtk.ui.timeline import Timeline, CURVE_SINE
from deepin_utils.process import run_command
from deepin_utils.math_lib import solve_parabola
from deepin_utils.file import read_file, write_file, touch_file, get_parent_dir
from deepin_utils.multithread import create_thread
from dtk.ui.utils import container_remove_all, set_cursor, get_widget_root_coordinate
from dtk.ui.application import Application
from dtk.ui.statusbar import Statusbar
from home_page import HomePage
from uninstall_page import UninstallPage
from install_page import InstallPage
from upgrade_page import UpgradePage
from data_manager import DataManager
import gtk
import dbus
import dbus.service
import time
from constant import (
            DSC_SERVICE_NAME, DSC_SERVICE_PATH, 
            DSC_FRONTEND_NAME, DSC_FRONTEND_PATH, 
            ACTION_INSTALL, ACTION_UNINSTALL, ACTION_UPGRADE,
            #PKG_STATUS_INSTALLED, PKG_STATUS_UNINSTALLED, PKG_STATUS_UPGRADED,
            CONFIG_DIR, ONE_DAY_SECONDS,
        )
from dtk.ui.slider import HSlider
from events import global_event
from dtk.ui.label import Label
from dtk.ui.gio_utils import start_desktop_file
from dtk.ui.iconview import IconView
from dtk.ui.treeview import TreeView
from start_desktop_window import StartDesktopWindow
from utils import is_64bit_system, handle_dbus_reply, handle_dbus_error, bit_to_human_str
import utils
from tooltip import ToolTip
from preference import preference_dialog

tool_tip = ToolTip()

def log(message):
    global debug_flag
    if debug_flag:
        print message

global current_status_pkg_page
current_status_pkg_page = None
def update_current_status_pkg_page(obj):
    global current_status_pkg_page
    current_status_pkg_page = obj

def refresh_current_page_status(pkg_name, pkg_info_list, bus_interface):
    change_pkgs = [info[0] for info in pkg_info_list]
    if isinstance(current_status_pkg_page, IconView):
        for item in current_status_pkg_page.items:
            if item.pkg_name in change_pkgs:
                item.is_installed = bus_interface.request_pkgs_install_status([pkg_name])[0]
                item.emit_redraw_request()
    elif isinstance(current_status_pkg_page, DetailPage):
        if current_status_pkg_page.pkg_name in change_pkgs:
            current_status_pkg_page.fetch_pkg_status()
    elif isinstance(current_status_pkg_page, TreeView):
        for item in current_status_pkg_page.visible_items:
            if item.pkg_name in change_pkgs:
                item.is_installed = bus_interface.request_pkgs_install_status([pkg_name])[0]
                current_status_pkg_page.redraw_request(item, True)

def update_navigatebar_number(navigatebar, page_index, notify_number):
    navigatebar.update_notify_num(navigatebar.nav_items[page_index], notify_number)

def jump_to_category(page_switcher, page_box, home_page, detail_page, first_category_name, second_category_name):
    switch_page(page_switcher, page_box, home_page, detail_page)
    home_page.jump_to_category(first_category_name, second_category_name)

def start_pkg(pkg_name, desktop_infos, (offset_x, offset_y, popup_x, popup_y), window):
    desktop_infos = filter(lambda desktop_info: os.path.exists(desktop_info[0]) != None, desktop_infos)
    desktop_infos_num = len(desktop_infos)
    if desktop_infos_num == 0:
        global_event.emit("show-message", "%s haven't any desktop file" % (pkg_name))
    elif desktop_infos_num == 1:
        start_desktop(pkg_name, desktop_infos[0][0])
    else:
        (screen, px, py, modifier_type) = window.get_display().get_pointer()
        StartDesktopWindow().start(pkg_name, desktop_infos, (px - offset_x + popup_x, py - offset_y + popup_y))
        
def start_desktop(pkg_name, desktop_path):
    global_event.emit("show-message", "%s: 已经发送启动请求" % (pkg_name))
    result = start_desktop_file(desktop_path)                    
    if result != True:
        global_event.emit("show-message", result)
    
def show_message(statusbar, message_box, message, hide_timeout=5000):
    hide_message(message_box)
    
    label = Label("%s" % message, enable_gaussian=True)
    label_align = gtk.Alignment()
    label_align.set(0.0, 0.5, 0, 0)
    label_align.set_padding(0, 0, 10, 0)
    label_align.add(label)
    message_box.add(label_align)
    
    statusbar.show_all()

    if hide_timeout:
        gtk.timeout_add(5000, lambda : hide_message(message_box))
    
def hide_message(message_box):
    container_remove_all(message_box)
    return False

def request_status_reply_hander(result, install_page, upgrade_page, uninstall_page):
    (download_status, action_status) = map(eval, result)
    
    install_page.update_download_status(download_status[ACTION_INSTALL])
    install_page.update_action_status(action_status[ACTION_INSTALL])
    
    upgrade_page.update_download_status(download_status[ACTION_UPGRADE])
    upgrade_page.update_action_status(action_status[ACTION_UPGRADE])
    
    uninstall_page.update_action_status(action_status[ACTION_UNINSTALL])

def grade_pkg(window, pkg_name, star):
    grade_config_path = os.path.join(CONFIG_DIR, "grade_pkgs")
    if not os.path.exists(grade_config_path):
        touch_file(grade_config_path)
        
    grade_config_str = read_file(grade_config_path)
    try:
        grade_config = eval(grade_config_str)

        if type(grade_config).__name__ != "dict":
            grade_config = {}
    except Exception:
        grade_config = {}
        
    current_time = time.time()    
    if not grade_config.has_key(pkg_name) or (current_time - grade_config[pkg_name]) > ONE_DAY_SECONDS:
        show_tooltip(window, "发送评分...")
        
        # Send grade to server.
        result = True
        
        if result:
            show_tooltip(window, "评分成功， 感谢您的参与！ :)")
            
            grade_config[pkg_name] = current_time
            write_file(grade_config_path, str(grade_config))
    else:
        show_tooltip(window, "您已经评过分了哟！ ;)")

def show_tooltip(window, message):
    tool_tip.set_text(message)
    (screen, px, py, modifier_type) = window.get_display().get_pointer()
    tool_tip.show_all()
    tool_tip.move(px, py)
    gtk.timeout_add(2000, tool_tip.hide_all)
    
def switch_from_detail_page(page_switcher, detail_page, page_box):
    page_switcher.slide_to_page(page_box, "left")
    
def switch_to_detail_page(page_switcher, detail_page, pkg_name):
    log("start switch to detail_page")
    page_switcher.slide_to_page(detail_page, "right")
    # ThreadMethod(detail_page.update_pkg_info, (pkg_name,)).start()
    detail_page.update_pkg_info(pkg_name)
    log("end switch to detail_page")
    global_event.emit("update-current-status-pkg-page", detail_page)

def switch_page(page_switcher, page_box, page, detail_page):
    start = time.time()
    log("slide to page")
    if page_switcher.active_widget == detail_page:
        page_switcher.slide_to_page(page_box, "left")
    else:
        page_switcher.slide_to_page(page_box, "right")
        
    log("remove widgets from page_box")
    container_remove_all(page_box)
    
    log("page_box pack widgets")
    page_box.pack_start(page, True, True)
    
    log("page_box show all")
    page_box.show_all()
    
    log("init widget in page_box")
    if isinstance(page, HomePage):
        log("page.recommend_item.show_page()")
        page.recommend_item.show_page()
            
        log("page.category_view.select_first_item()")
        page.category_view.select_first_item()
    elif isinstance(page, UpgradePage):
        page.fetch_upgrade_info()
        if page.in_no_notify_page:
            page.show_init_page()
    print "Switch Page: %s" % (time.time()-start, )

def message_handler(messages, bus_interface, upgrade_page, uninstall_page, install_page, home_page):
    for message in messages:
        (signal_type, action_content) = message

        if signal_type == "ready-download-start":
            (pkg_name, action_type) = action_content
            if action_type == ACTION_INSTALL:
                install_page.download_ready(pkg_name)
            elif action_type == ACTION_UPGRADE:
                upgrade_page.download_ready(pkg_name)

        elif signal_type == 'ready-download-finish':
            (pkg_name, action_type) = action_content
            if action_type == ACTION_INSTALL:
                install_page.download_wait(pkg_name)
            elif action_type == ACTION_UPGRADE:
                upgrade_page.download_wait(pkg_name)

        elif signal_type == "download-start":
            (pkg_name, action_type) = action_content
            if action_type == ACTION_INSTALL:
                install_page.download_start(pkg_name)
            elif action_type == ACTION_UPGRADE:
                upgrade_page.download_start(pkg_name)

        elif signal_type == "download-update":
            (pkg_name, action_type, percent, speed) = action_content
            if action_type == ACTION_INSTALL:
                install_page.download_update(pkg_name, percent, speed)
            elif action_type == ACTION_UPGRADE:
                upgrade_page.download_update(pkg_name, percent, speed)

        elif signal_type == "download-finish":
            (pkg_name, action_type) = action_content
            if action_type == ACTION_INSTALL:
                install_page.download_finish(pkg_name)
            elif action_type == ACTION_UPGRADE:
                upgrade_page.download_finish(pkg_name)

        elif signal_type == "download-stop":
            (pkg_name, action_type) = action_content
            if action_type == ACTION_INSTALL:
                install_page.download_stop(pkg_name)
            elif action_type == ACTION_UPGRADE:
                upgrade_page.download_stop(pkg_name)

        elif signal_type == "action-start":
            (pkg_name, action_type) = action_content
            if action_type == ACTION_UNINSTALL:
                uninstall_page.action_start(pkg_name)
            elif action_type == ACTION_UPGRADE:
                upgrade_page.action_start(pkg_name)
            elif action_type == ACTION_INSTALL:
                install_page.action_start(pkg_name)

        elif signal_type == "action-update":
            (pkg_name, action_type, percent, status) = action_content
            if action_type == ACTION_UNINSTALL:
                uninstall_page.action_update(pkg_name, percent)
            elif action_type == ACTION_UPGRADE:
                upgrade_page.action_update(pkg_name, percent)
            elif action_type == ACTION_INSTALL:
                install_page.action_update(pkg_name, percent)

        elif signal_type == "action-finish":
            (pkg_name, action_type, pkg_info_list) = action_content
            if action_type == ACTION_UNINSTALL:
                uninstall_page.action_finish(pkg_name, pkg_info_list)
            elif action_type == ACTION_UPGRADE:
                upgrade_page.action_finish(pkg_name, pkg_info_list)
            elif action_type == ACTION_INSTALL:
                install_page.action_finish(pkg_name, pkg_info_list)
            
            refresh_current_page_status(pkg_name, pkg_info_list, bus_interface)

        elif signal_type == "update-list-finish":
            upgrade_page.fetch_upgrade_info()
            bus_interface.request_status(
                    reply_handler=lambda reply: request_status_reply_hander(reply, install_page, upgrade_page, uninstall_page),
                    error_handler=handle_dbus_error
                    )
            global_event.emit("show-message", "软件列表更新完成!", 0)

        elif signal_type == "update-list-update":
            upgrade_page.update_upgrade_progress(action_content)
            percent = "%.2f%%" % float(action_content)
            global_event.emit("show-message", "更新软件列表: %s" % percent)

        elif signal_type == "parse-download-error":
            (pkg_name, action_type) = action_content
            if action_type == ACTION_INSTALL:
                install_page.download_parse_failed(pkg_name)
                global_event.emit("show-message", "分析%s依赖出现问题， 安装停止" % pkg_name)
            elif action_type == ACTION_UPGRADE:
                upgrade_page.download_parse_failed(pkg_name)
                global_event.emit("show-message", "分析%s依赖出现问题， 升级停止" % pkg_name)

        elif signal_type == "got-install-deb-pkg-name":
            pkg_name = action_content
            install_page.add_install_actions([pkg_name])

        elif signal_type == "pkg-not-in-cache":
            pkg_name = action_content
            if is_64bit_system():
                message = "%s在64位系统上不能被安装" % pkg_name
            else:
                message = "%s在32位系统上不能被安装，该包可能位64位系统特有的包" % pkg_name
            global_event.emit("show-message", message)
    
    return True

install_stop_list = []
def request_stop_install_actions(pkg_names):
    global install_stop_list
    
    install_stop_list += pkg_names
    
def clear_install_stop_list(install_page):
    global install_stop_list
    
    if len(install_stop_list) > 0:
        for pkg_name in install_stop_list:
            for item in install_page.treeview.visible_items:
                if item.pkg_name == pkg_name:
                    install_page.treeview.delete_items([item])
                    break
                
        install_stop_list = []        
        
    return True    

def install_pkg(bus_interface, install_page, pkg_names, window):
    # Add install animation.
    (screen, px, py, modifier_type) = window.get_display().get_pointer()
    ax, ay = px, py
    
    (wx, wy) = window.window.get_origin()
    offset_bx = 430
    offset_by = -20
    bx, by = wx + offset_bx, wy + offset_by
    
    offset_cx = 10
    offset_cy = 10
    if ax < bx:
        cx, cy = wx + offset_bx + offset_cx, wy + offset_by + offset_cy
    else:
        cx, cy = wx + offset_bx - offset_cx, wy + offset_by + offset_cy
    
    [[a], [b], [c]] = solve_parabola((ax, ay), (bx, by), (cx, cy))
    
    icon_window = IconWindow(pkg_names[0])
    icon_window.move(ax, ay)
    icon_window.show_all()
    
    timeline = Timeline(500, CURVE_SINE)
    timeline.connect("update", lambda source, status: update(source, status, icon_window, (ax, ay), (bx, by), (cx, cy), (a, b, c)))
    timeline.connect("completed", lambda source: finish(source, icon_window, bus_interface, pkg_names))
    timeline.run()
    
    # Add to install page.
    #install_page.add_install_actions(pkg_names)

    
def update(source, status, icon_window, (ax, ay), (bx, by), (cx, cy), (a, b, c)):
    move_x = ax + (cx - ax) * status
    move_y = a * pow(move_x, 2) + b * move_x + c
    
    icon_window.move(int(move_x), int(move_y))
    icon_window.show_all()
    
def finish(source, icon_window, bus_interface, pkg_names):
    icon_window.destroy()

    # Send install command.
    create_thread(lambda : bus_interface.install_pkg(
                                pkg_names, 
                                reply_handler=handle_dbus_reply, 
                                error_handler=handle_dbus_error)).start()
    
clear_failed_action_dict = {
    ACTION_INSTALL : [],
    ACTION_UPGRADE : [],
    }
def request_clear_failed_action(pkg_name, action_type):
    global clear_failed_action_dict
    
    if action_type == ACTION_INSTALL:
        clear_failed_action_dict[ACTION_INSTALL].append(pkg_name)
    elif action_type == ACTION_UPGRADE:
        clear_failed_action_dict[ACTION_UPGRADE].append(pkg_name)
        
def clear_failed_action(install_page, upgrade_page):
    global clear_failed_action_dict
    
    install_items = []
    upgrade_items = []

    for pkg_name in clear_failed_action_dict[ACTION_INSTALL]:
        for item in install_page.treeview.visible_items:
            if item.pkg_name == pkg_name:
                install_items.append(item)

    for pkg_name in clear_failed_action_dict[ACTION_UPGRADE]:
        for item in upgrade_page.upgrade_treeview.visible_items:
            if item.pkg_name == pkg_name:
                upgrade_items.append(item)
                
    install_page.treeview.delete_items(install_items)            
    upgrade_page.upgrade_treeview.delete_items(upgrade_items)            
    
    clear_failed_action_dict = {
        ACTION_INSTALL : [],
        ACTION_UPGRADE : [],
        }
    
    return True
    
clear_action_list = []
def request_clear_action_pages(pkg_info_list):
    global clear_action_list
    
    clear_action_list += pkg_info_list

def clear_action_pages(bus_interface, upgrade_page, uninstall_page, install_page):
    global clear_action_list
    
    if len(clear_action_list) > 0:
        
        # Delete items from treeview.
        installed_items = []
        uninstalled_items = []
        upgraded_items = []
        install_pkgs = []
        
        for (pkg_name, marked_delete, marked_install, marked_upgrade) in clear_action_list:
            if marked_delete:
                for item in uninstall_page.treeview.visible_items:
                    if item.pkg_name == pkg_name:
                        uninstalled_items.append(item)
                        break
            elif marked_install:
                for item in install_page.treeview.visible_items:
                    if item.pkg_name == pkg_name:
                        installed_items.append(item)
                        
                        install_pkgs.append(pkg_name)
                        break
            elif marked_upgrade:
                for item in upgrade_page.upgrade_treeview.visible_items:
                    if item.pkg_name == pkg_name:
                        upgraded_items.append(item)
                        
                        install_pkgs.append(pkg_name)
                        break
                    
        uninstall_page.delete_uninstall_items(uninstalled_items)
        install_page.treeview.delete_items(installed_items)
        upgrade_page.upgrade_treeview.delete_items(upgraded_items)
        
        # Add installed package in uninstall page.
        for item in uninstall_page.treeview.visible_items:
            if item.pkg_name in install_pkgs:
                install_pkgs.remove(item.pkg_name)

        install_pkg_versions = bus_interface.request_pkgs_install_version(install_pkgs)
        install_pkg_infos = []
        for (pkg_name, pkg_version) in zip(install_pkgs, install_pkg_versions):
            install_pkg_infos.append(str((str(pkg_name), str(pkg_version))))
        uninstall_page.add_uninstall_items(install_pkg_infos)
        
        clear_action_list = []
        
    return True    
    
debug_flag = False                

class DeepinSoftwareCenter(dbus.service.Object):
    '''
    class docs
    '''

    def __init__(self, session_bus, arguments):
        '''
        init docs
        '''
        dbus.service.Object.__init__(self, session_bus, DSC_FRONTEND_PATH)
        
        self.simulate = "--simulate" in arguments
        
        global debug_flag
        debug_flag = "--debug" in arguments

    def exit(self):
        gtk.main_quit()
        
    def open_download_directory(self):
        run_command("xdg-open /var/cache/apt/archives")
        
    def switch_page(self, page):
        switch_page(self.page_switcher, self.page_box, page, self.detail_page)
        
    def show_home_page(self):
        if self.detail_page and self.home_page:
            self.switch_page(self.home_page)
    
    def show_upgrade_page(self):
        if self.detail_page and self.upgrade_page:
            self.switch_page(self.upgrade_page)
    
    def show_uninstall_page(self):
        if self.detail_page and self.uninstall_page:
            self.switch_page(self.uninstall_page)
    
    def show_install_page(self):
        if self.detail_page and self.install_page:
            self.switch_page(self.install_page)
        
    def init_ui(self):
        print "init ui"
        # Init application.
        image_dir = os.path.join(get_parent_dir(__file__, 2), "image")
        self.application = Application(resizable=False)
        self.application.set_default_size(888, 634)
        self.application.set_skin_preview(os.path.join(image_dir, "frame.png"))
        self.application.set_icon(os.path.join(image_dir, "icon.png"))
        self.application.add_titlebar(
                ["theme", "menu", "min", "close"],
                show_title=False
                )
        self.application.window.set_title(_("Deepin Software Center"))
        
        # Init page box.
        self.page_box = gtk.VBox()
        
        # Init page switcher.
        self.page_switcher = HSlider(200)
        self.page_switcher.append_page(self.page_box)
        self.page_switcher.set_to_page(self.page_box)
        
        # Init page align.
        page_align = gtk.Alignment()
        page_align.set(0.5, 0.5, 1, 1)
        page_align.set_padding(0, 0, 2, 2)
        
        # Append page to switcher.
        page_align.add(self.page_switcher)
        self.application.main_box.pack_start(page_align, True, True)
        
        # Init status bar.
        self.statusbar = Statusbar(24)
        status_box = gtk.HBox()
        self.message_box = gtk.HBox()
        join_us_button = LinkButton("加入我们", "http://www.linuxdeepin.com/joinus/job")
        join_us_button_align = gtk.Alignment()
        join_us_button_align.set(0.5, 0.5, 0, 0)
        join_us_button_align.set_padding(0, 3, 0, 10)
        join_us_button_align.add(join_us_button)
        status_box.pack_start(self.message_box, True, True)
        status_box.pack_start(join_us_button_align, False, False)
        self.statusbar.status_box.pack_start(status_box, True, True)
        self.application.main_box.pack_start(self.statusbar, False, False)
        
        # Init navigatebar.
        self.detail_page = None
        self.home_page = None
        self.upgrade_page = None
        self.uninstall_page = None
        self.install_page = None
        
        self.navigatebar = Navigatebar(
                [
                (app_theme.get_pixbuf("navigatebar/nav_recommend.png"), " 软件中心", self.show_home_page),
                (app_theme.get_pixbuf("navigatebar/nav_update.png"), " 系统升级", self.show_upgrade_page),
                (app_theme.get_pixbuf("navigatebar/nav_uninstall.png"), " 卸载软件", self.show_uninstall_page),
                (app_theme.get_pixbuf("navigatebar/nav_download.png"), " 安装管理", self.show_install_page),
                ],
                font_size = 11,
                padding_x = 2,
                padding_y = 2,
                vertical=False,
                item_hover_pixbuf=app_theme.get_pixbuf("navigatebar/nav_hover.png"),
                item_press_pixbuf=app_theme.get_pixbuf("navigatebar/nav_press.png"),
                )
        self.navigatebar.set_size_request(-1, 56)
        self.navigatebar_align = gtk.Alignment(0, 0, 1, 1)
        self.navigatebar_align.set_padding(0, 0, 4, 0)
        self.navigatebar_align.add(self.navigatebar)
        self.application.titlebar.set_size_request(-1, 56)
        self.application.titlebar.left_box.pack_start(self.navigatebar_align, True, True)
        self.application.window.add_move_event(self.navigatebar)
        
        # Init menu.
        menu = Menu(
            [
             (None, "打开下载目录", self.open_download_directory),
             (None, "智能清理下载文件", self.clean_download_cache),
             (None, "显示新功能", lambda : self.show_wizard_win()),
             (None, "选项", lambda : preference_dialog.show_all()),
             (None, "退出", self.exit),
             ],
            is_root_menu=True,
            menu_min_width=150,
            )
        self.application.set_menu_callback(
            lambda button:
                menu.show(
                get_widget_root_coordinate(button, WIDGET_POS_BOTTOM_LEFT),
                (button.get_allocation().width, 0)))
        
        # Make window can received drop data.
        targets = [("text/uri-list", 0, 1)]        
        self.application.window.drag_dest_set(gtk.DEST_DEFAULT_MOTION | gtk.DEST_DEFAULT_DROP, targets, gtk.gdk.ACTION_COPY)
        #self.application.window.connect_after("drag-data-received", self.on_drag_data_received)        
        
        start = time.time()
        self.init_home_page()
        print "Finish Init UI: %s" % (time.time()-start, )
        
        self.ready_show()
        
    def ready_show(self):    
        if utils.is_first_started():
            self.show_wizard_win(True, callback=self.wizard_callback)
            utils.set_first_started()
        else:    
            self.application.window.show_all()
        gtk.main()    
        
    def show_wizard_win(self, show_button=False, callback=None):    
        import locale
        (lang, encode) = locale.getdefaultlocale()
        program_dir = get_parent_dir(__file__, 2)
        if lang == "zh_CN":
            wizard_dir = os.path.join(program_dir, "wizard", "zh_CN")
        elif lang in ["zh_HK", "zh_TW"]:
            wizard_dir = os.path.join(program_dir, "wizard", "zh_HK")
        else:    
            wizard_dir = os.path.join(program_dir, "wizard", "en")
        wizard_root_dir = os.path.dirname(wizard_dir)            
            
        Wizard(
            [os.path.join(wizard_dir, "%d.jpg" % i) for i in range(3)],
            (os.path.join(wizard_root_dir, "dot_normal.png"),
             os.path.join(wizard_root_dir, "dot_active.png"),             
             ),
            (os.path.join(wizard_dir, "start_normal.png"),
             os.path.join(wizard_dir, "start_press.png"),             
             ),
            show_button,
            callback
            ).show_all()
        
    def wizard_callback(self):
        self.application.window.show_all()
        gtk.timeout_add(100, self.application.raise_to_top)
        
    def init_home_page(self):
        
        # Init DBus.
        self.system_bus = dbus.SystemBus()
        bus_object = self.system_bus.get_object(DSC_SERVICE_NAME, DSC_SERVICE_PATH)
        self.bus_interface = dbus.Interface(bus_object, DSC_SERVICE_NAME)
        # Say hello to backend. 
        #self.bus_interface.say_hello(self.simulate)
        
        log("Init data manager")
        
        # Init data manager.
        self.data_manager = DataManager(self.bus_interface)

        # Init packages status
        self.packages_status = {}
        
        log("Init home page.")
        self.home_page = HomePage(self.data_manager)
        
        log("Init switch page.")
        self.switch_page(self.home_page)
        
        self.init_backend()
        
    def init_backend(self):
        
        log("Init detail view")
        
        # Init detail view.
        self.detail_page = DetailPage(self.data_manager)
        
        self.page_switcher.append_page(self.detail_page)
        
        log("Init pages.")
        
        # Init pages.
        start = time.time()
        log("Init upgrade page.")
        self.upgrade_page = UpgradePage(self.bus_interface, self.data_manager)
        log("Init uninstall page.")
        self.uninstall_page = UninstallPage(self.bus_interface, self.data_manager)
        log("Init install page.")
        self.install_page = InstallPage(self.bus_interface, self.data_manager)
        print "Init three pages time: %s" % (time.time()-start, )
        
        self.bus_interface.request_status(
                reply_handler=lambda reply: request_status_reply_hander(reply, self.install_page, self.upgrade_page, self.uninstall_page),
                error_handler=handle_dbus_error
                )
        
        log("Handle global event.")
        
        # Handle global event.
        global_event.register_event("install-pkg", lambda pkg_names: install_pkg(self.bus_interface, self.install_page, pkg_names, self.application.window))
        global_event.register_event("upgrade-pkg", lambda pkg_names: gtk.timeout_add(10, self.upgrade_pkg, pkg_names))
        global_event.register_event("uninstall-pkg", self.bus_interface.uninstall_pkg)
        global_event.register_event("stop-download-pkg", self.bus_interface.stop_download_pkg)
        global_event.register_event("switch-to-detail-page", lambda pkg_name : switch_to_detail_page(self.page_switcher, self.detail_page, pkg_name))
        global_event.register_event("switch-from-detail-page", lambda : switch_from_detail_page(self.page_switcher, self.detail_page, self.page_box))
        global_event.register_event("remove-wait-action", self.bus_interface.remove_wait_missions)
        global_event.register_event("remove-wait-download", self.bus_interface.remove_wait_downloads)
        global_event.register_event("request-clear-action-pages", request_clear_action_pages)
        global_event.register_event("request-stop-install-actions", request_stop_install_actions)
        global_event.register_event("request-clear-failed-action", request_clear_failed_action)
        global_event.register_event("update-upgrade-notify-number", lambda number: update_navigatebar_number(self.navigatebar, 1, number))        
        global_event.register_event("update-install-notify-number", lambda number: update_navigatebar_number(self.navigatebar, 3, number))        
        global_event.register_event("jump-to-category", 
                                    lambda first_category_name, second_category_name: 
                                    jump_to_category(self.page_switcher, 
                                                     self.page_box, 
                                                     self.home_page, 
                                                     self.detail_page, 
                                                     first_category_name, 
                                                     second_category_name))
        global_event.register_event("grade-pkg", lambda pkg_name, star: grade_pkg(self.application.window, pkg_name, star))
        global_event.register_event("set-cursor", lambda cursor: set_cursor(self.application.window, cursor))
        global_event.register_event("show-message", self.update_status_bar_message)
        global_event.register_event("start-pkg", lambda pkg_name, desktop_infos, offset: start_pkg(pkg_name, desktop_infos, offset, self.application.window))
        global_event.register_event("start-desktop", start_desktop)
        global_event.register_event("show-pkg-name-tooltip", lambda pkg_name: show_tooltip(self.application.window, pkg_name))
        global_event.register_event("update-current-status-pkg-page", update_current_status_pkg_page)
        self.system_bus.add_signal_receiver(
            lambda messages: message_handler(messages, 
                                         self.bus_interface, 
                                         self.upgrade_page, 
                                         self.uninstall_page, 
                                         self.install_page,
                                         self.home_page),
            dbus_interface=DSC_SERVICE_NAME, 
            path=DSC_SERVICE_PATH, 
            signal_name="update_signal")
        glib.timeout_add(1000, lambda : clear_action_pages(self.bus_interface, self.upgrade_page, self.uninstall_page, self.install_page))
        glib.timeout_add(1000, lambda : clear_install_stop_list(self.install_page))
        glib.timeout_add(1000, lambda : clear_failed_action(self.install_page, self.upgrade_page))

        
        create_thread(self.request_update_list).start()
        log("finish")
        #for event in global_event.events:
            #print "%s: %s" % (event, global_event.events[event])

    def update_status_bar_message(self, message, hide_timeout=5000):
        if hide_timeout:
            show_message(self.statusbar, self.message_box, message)
        else:
            show_message(self.statusbar, self.message_box, message, hide_timeout)

    def request_update_list(self):
        self.bus_interface.start_update_list(
                reply_handler=handle_dbus_reply,
                error_handler=handle_dbus_error,)

    def upgrade_pkg(self, pkg_names):
        self.bus_interface.upgrade_pkg(pkg_names, reply_handler=handle_dbus_reply, error_handler=handle_dbus_error)
        return False

    def clean_download_cache(self):
        self.bus_interface.clean_download_cache(
                reply_handler=self.clean_download_cache_reply, 
                error_handler=handle_dbus_error)

    def clean_download_cache_reply(obj, result):
        print result
        num, size = result
        if num != 0:
            message = "恭喜您清理了%s个软件包，共节约了%s空间" % (num, bit_to_human_str(size))
            print message
        else:
            message = "您的系统已经很干净了，不需要清理."
        global_event.emit("show-message", message, 0)

    def run(self):    
        self.init_ui()
        
        log("Send exit request to backend when frontend exit.")
        
        # Send exit request to backend when frontend exit.
        self.bus_interface.request_quit()
        
        # Remove id from config file.
        data_exit()

    @dbus.service.method(DSC_FRONTEND_NAME, in_signature="as", out_signature="")    
    def hello(self, arguments):
        self.application.raise_to_top()
        
        #deb_files = filter(self.is_deb_file, arguments)        
        #if len(deb_files) > 0:
            #self.bus_interface.install_deb_files(deb_files)
        
    @dbus.service.signal(DSC_FRONTEND_NAME)
    def update_signal(self, message):
        pass
