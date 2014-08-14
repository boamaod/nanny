#!/usr/bin/env python
#
# Copyright (C) 2009,2010,2011 Junta de Andalucia
# Copyright (C) 2012 Guido Tabbernuk
# 
# Authors:
#   Roberto Majadas <roberto.majadas at openshine.com>
#   Guido Tabbernuk <boamaod at gmail.com>
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
import dbus

from twisted.internet import reactor, threads

from subprocess import Popen, PIPE
from signal import SIGTERM

import time
import psutil

import traceback # for debugging

(
SESSION_APPID,
WEB_APPID,
MAIL_APPID,
IM_APPID) = range(4)

class LinuxSessionBlocker(gobject.GObject) :
    def __init__(self, quarterback, session_blocker="nanny-desktop-blocker"):
        gobject.GObject.__init__(self)
        self.quarterback = quarterback
        self.sb = session_blocker
        self.block_status = []

    def is_user_blocked(self, user_id):
        if user_id in self.block_status:
            return True
        else:
            return False

    def blocker_terminate_from_thread(self, user_id, ret):
        print "[LinuxSessionFiltering] self.blocker_terminate_from_thread %s %s" % (user_id, ret)

        if ret == 222: #special code for calling to kill session

            try:
                d = dbus.SystemBus()
                login1_object = d.get_object("org.freedesktop.login1", "/org/freedesktop/login1")
                manager_iface = dbus.Interface(login1_object, "org.freedesktop.login1.Manager")

                sessions = manager_iface.ListSessions()

                for session in sessions:
                    if session[1] != user_id:
                        continue

                    session_object = d.get_object("org.freedesktop.login1", session[4])
                    props_iface = dbus.Interface(session_object, 'org.freedesktop.DBus.Properties')
                    sess_type = props_iface.Get("org.freedesktop.login1.Session", 'Type')

                    if sess_type == "x11" :
                        print "kill", session_object
                        session_object.Kill("all", SIGTERM)
            except:
                print traceback.format_exc()
                for proc in psutil.process_iter():
                    if proc.uids[0] != user_id:
                        continue
                    try:
                        cmd = proc.cmdline
                    except:
                        print "Process", proc, "command line not found"
                        continue
                    if len(cmd)==0:
                        continue
                    cmd = cmd[0]
                    if cmd == "x-session-manager" or cmd == "/usr/bin/x-session-manager" or cmd == "/usr/bin/gnome-session" or cmd == "gnome-session" or cmd == "/usr/bin/lxsession" or cmd == "lxsession":
                        exec_cmd = "kill -9 %s" % (proc.pid)
                        print "Executing fallback:", exec_cmd, cmd
                        Popen(exec_cmd, shell=True, stdout=PIPE)

        elif ret > 100: # serious error codes

            print "[LinuxSessionFiltering] User or other try to kill blocker :)"
            gobject.timeout_add(5000, self.__launch_blocker_to_badboy, user_id)
            
            return

        gobject.timeout_add(5000, self.__remove_block_status, user_id)

    def set_block(self, user_id, block_status):
        if block_status == True:
            if user_id not in self.block_status :
                self.__launch_blocker(user_id)
        else:
            try:
                self.block_status.pop(self.block_status.index(user_id))
            except:
                pass

    def __remove_block_status(self, user_id):
        print "[LinuxSessionFiltering] Remove block status to user_id :  %s" % (user_id)
        try:
            self.block_status.pop(self.block_status.index(user_id))
        except:
            pass
        return False

    def __launch_blocker_to_badboy(self, user_id):
        x11_display = self.__get_user_session_display(user_id)

        if x11_display is not None :
            print "[LinuxSessionFiltering] badboy blocking user %s for display %s" % (user_id, x11_display)
            user_name = self.quarterback.usersmanager.get_username_by_uid(user_id)
            reactor.callInThread(self.__launch_blocker_thread, user_id, user_name, x11_display, self, True)
        else:
            self.block_status.pop(self.block_status.index(user_id))
        return False

    def __launch_blocker(self, user_id):
        x11_display = self.__get_user_session_display(user_id)

        if x11_display is not None :
            self.block_status.append(user_id)
            print "[LinuxSessionFiltering] blocking user %s for display %s" % (user_id, x11_display)
            user_name = self.quarterback.usersmanager.get_username_by_uid(user_id)
            reactor.callInThread(self.__launch_blocker_thread, user_id, user_name, x11_display, self)
        
    def __launch_blocker_thread(self, user_id, user_name, x11_display, linuxsb, badboy = False):
        try:
            proclist = []
            for proc in psutil.process_iter():
                if proc.uids[0] != int(user_id):
                    continue
                proclist.append(proc)
            proclist.reverse() # start from the newest process
            env_lang_var = 'C'
            for proc in proclist:
                lang_var = Popen('cat /proc/%s/environ | tr "\\000" "\\n" | grep ^LANG= ' % proc.pid , 
                                 shell=True, stdout=PIPE).stdout.readline().strip("\n")
                if len(lang_var) > 0 :
                    env_lang_var = lang_var.replace("LANG=","")
                    break

            # hack to start after desktop has actually been loaded
            # see https://bugs.launchpad.net/nanny/+bug/916788 etc
            #
            # BOH
            env_session_type = None
            for proc in proclist:
                session_type = Popen('cat /proc/%s/environ | tr "\\000" "\\n" | grep ^DESKTOP_SESSION= ' % proc.pid, 
                                 shell=True, stdout=PIPE).stdout.readline().strip("\n")
                if len(session_type) > 0 :
                    env_session_type = session_type.replace("DESKTOP_SESSION=","")
                    break
            
            print "DESKTOP_SESSION=" + env_session_type
            
            DEFAULT_SLEEP_TIME = 36
            SLEEP_INTERVAL = 2
            INTERVALS = INTERVALS_COUNT = 18
            
            if env_session_type in ("ubuntu", "ubuntu-2d"):
                while os.system("pgrep -u %i -fla unity-panel-service | grep -v pgrep" % user_id) != 0 and INTERVALS > 0: 
                    INTERVALS = INTERVALS - 1
                    print "Waiting for the desktop to start", INTERVALS
                    time.sleep(SLEEP_INTERVAL)

            elif env_session_type == "gnome-classic":
                while os.system("pgrep -u %i -fla gnome-panel | grep -v pgrep" % user_id) != 0 and INTERVALS > 0: 
                    INTERVALS = INTERVALS - 1
                    print "Waiting for the desktop to start", INTERVALS
                    time.sleep(SLEEP_INTERVAL)

            elif env_session_type == "gnome-shell":
                while os.system("pgrep -u %i -fla gnome-shell | grep -v pgrep" % user_id) != 0 and INTERVALS > 0: 
                    INTERVALS = INTERVALS - 1
                    print "Waiting for the desktop to start", INTERVALS
                    time.sleep(SLEEP_INTERVAL)
                SLEEP_INTERVAL = DEFAULT_SLEEP_TIME - (INTERVALS_COUNT*SLEEP_INTERVAL - INTERVALS*SLEEP_INTERVAL)

            elif env_session_type in ("Lubuntu", "LXDE"):
                while os.system("pgrep -u %i -fla lxpanel | grep -v pgrep" % user_id) != 0 and INTERVALS > 0: 
                    INTERVALS = INTERVALS - 1
                    print "Waiting for the desktop to start", INTERVALS
                    time.sleep(SLEEP_INTERVAL)

            else:
                print "Sleeping for %s seconds just like that..." % DEFAULT_SLEEP_TIME
                time.sleep(DEFAULT_SLEEP_TIME)
                env_session_type = "ubuntu"

            print "Taking a %s second snooze before starting..." % SLEEP_INTERVAL
            time.sleep(SLEEP_INTERVAL)
            # EOH

            cmd = ['su', user_name, '-c', 
                   'LANG=%s DISPLAY=%s %s %s %s &>> /var/tmp/desktop-blocker-%s.log'
                   % (env_lang_var, x11_display, self.sb, env_session_type, "bad" if badboy else "", user_id)]
            print cmd
            p = Popen(cmd)
            
            print "[LinuxSessionFiltering] launching blocker (pid : %s)" % p.pid

            while p.poll() == None :
                time.sleep(1)
                b = threads.blockingCallFromThread(reactor, linuxsb.is_user_blocked, user_id)
                if b == False:
                    p.terminate()
                    print "[LinuxSessionFiltering] Unblocking session %s" % user_id
                    return

            print "[LinuxSessionFiltering] blocker terminated by user interaction"
            threads.blockingCallFromThread(reactor, linuxsb.blocker_terminate_from_thread, user_id, p.poll())
            
        except:
        
            print "[LinuxSessionFiltering] blocker terminated by exception"
            print traceback.format_exc()
            threads.blockingCallFromThread(reactor, linuxsb.blocker_terminate_from_thread, user_id, 223)

    def __get_user_session_display(self, user_id):
        d = dbus.SystemBus()
        login1_object = d.get_object("org.freedesktop.login1", "/org/freedesktop/login1")
        manager_iface = dbus.Interface(login1_object, "org.freedesktop.login1.Manager")

        sessions = manager_iface.ListSessions()
        for session in sessions :
            if session[1] != user_id :
                    continue
            session_object = d.get_object("org.freedesktop.login1", session[4])
            props_iface = dbus.Interface(session_object, 'org.freedesktop.DBus.Properties')
            x11_display = props_iface.Get("org.freedesktop.login1.Session", 'Display')
            active = props_iface.Get("org.freedesktop.login1.Session", 'Active')

            if x11_display != "" and active:
                return x11_display

        return None

class LinuxSessionFiltering(gobject.GObject) :
    def __init__(self, quarterback) :
        gobject.GObject.__init__(self)
        self.quarterback = quarterback
        
        reactor.addSystemEventTrigger("before", "startup", self.start)
        reactor.addSystemEventTrigger("before", "shutdown", self.stop)

        self.updater_session_hd = None

    def start(self):
        print "Start Linux Session Filtering"
        self.linuxsb = LinuxSessionBlocker(self.quarterback)
        if self.linuxsb.sb != None :
            print "[LinuxSessionFiltering] start watcher :)"
            self.updater_session_hd = gobject.timeout_add(1000, self.__update_session_blocker_status)

    def stop(self):
        print "Stop Linux Session Filtering"
        if self.updater_session_hd != None:
            gobject.source_remove(self.updater_session_hd)
        
        self.linuxsb.block_status = []
        reactor.iterate(delay=2)
        print "Stopped Linux Session Filtering"


    def __update_session_blocker_status(self):
        blocks = self.quarterback.blocks
        for user_id in blocks.keys() :
            for app_id in blocks[user_id].keys() :
                if app_id != SESSION_APPID :
                    continue

                if self.quarterback.get_available_time(user_id, app_id) == 0 :
                    self.linuxsb.set_block(int(user_id), True)
                    continue

                try:
                    block_status, next_block = self.quarterback.is_blocked(user_id, app_id)
                except:
                    print "[LinuxSessionFiltering] Fail getting self.quarterback.is_blocked"
                    block_status = False

                if block_status == True :
                    self.linuxsb.set_block(int(user_id), True)
                else:
                    self.linuxsb.set_block(int(user_id), False)

        return True
