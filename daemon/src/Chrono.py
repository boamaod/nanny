#!/usr/bin/env python

# Copyright (C) 2009,2010 Junta de Andalucia
# 
# Authors:
#   Roberto Majadas <roberto.majadas at openshine.com>
#   Cesar Garcia Tapia <cesar.garcia.tapia at openshine.com>
#   Luis de Bethencourt <luibg at openshine.com>
#   Pablo Vieytes <pvieytes at openshine.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301
# USA

import gobject
import os

import psutil

import pickle
import datetime

import dbus

(
SESSION_APPID,
WEB_APPID,
MAIL_APPID,
IM_APPID) = range(4)


class Chrono(gobject.GObject) :
    '''
    This class handles the use time of all application categories that
    Gnome Nanny controls.

    Application list is generated from files in:
        /var/lib/nanny/applists/
    There is one file per category and each line in the file is an
    application name.

    Max time of use per day of the categories is set in Gnome Nannys
    Admin Console.
    '''
    def __init__(self, quarterback): 
        '''Init function for NannyChrono class.'''
        gobject.GObject.__init__(self)
        self.quarterback = quarterback

        self.day = datetime.date.today().day
        self.categories = ['session', 'browser', 'email', 'im']

        self.quarterback.connect('block-status', self.__update_cb)

    def __update_cb(self, quarterback, block_status, user_id, app_id, next_change, available_time, active):
        '''Callback that updates the used times of the categories.'''
        if block_status == False:
            app_list = self.__get_application_list(self.categories)
            if app_id == SESSION_APPID :
                try:
                    d = dbus.SystemBus()
                    login1_object = d.get_object("org.freedesktop.login1", "/org/freedesktop/login1")
                    manager_iface = dbus.Interface(login1_object, "org.freedesktop.login1.Manager")

                    sessions = manager_iface.ListSessions()
                    for session in sessions :
                        if session[1] != int(user_id) :
                                continue
                        session_object = d.get_object("org.freedesktop.login1", session[4])
                        props_iface = dbus.Interface(session_object, 'org.freedesktop.DBus.Properties')
                        x11_display = props_iface.Get("org.freedesktop.login1.Session", 'Display')
                        active = props_iface.Get("org.freedesktop.login1.Session", 'Active')

                        if x11_display != "" and active:
                            self.quarterback.subtract_time(user_id, app_id)
                            break
                except:
                    print "Crash Chrono __update_cb"
            else:
                category = self.categories[app_id]
                for proc in psutil.process_iter():
                    cmd = ""
                    try:
                        if proc.uids[0] != user_id:
                            continue
                            cmd = psutil.Process(proc).cmdline
                    except:
                        print "Process", proc, "command line not found"
                        continue
                    if len(cmd)>0:
                        if self.is_a_controlled_app(cmd[0], category, app_list):
                            self.quarterback.subtract_time(user_id, app_id)
                            break

    def is_a_controlled_app(self, process, category, app_list):
        found = False

        for app in app_list:
            if app[0] == category:
                if os.path.basename(process) == app[1]:
                    found = True
                    break

        return found

    def __get_application_list(self, categories):
        '''Generate the application list from the app files.

        Format:
            app_list = [['browser', 'firefox'],
                        ['email', 'thunderbird']]
        '''

        app_list = [['session', 'gnome-session']]
        for category in categories:
            file_path = '/etc/nanny/applists/' + category
            if os.path.exists(file_path):
                file = open(file_path, 'rb')
                for line in file:
                    if len(line) > 1:
                        app_list.append([category, line[:-1]])

        return app_list


gobject.type_register(Chrono)
