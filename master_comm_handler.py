#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2017 Gabriele Iannetti <g.iannetti@gsi.de>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#


import zmq

from comm_handler import CommHandler


class MasterCommHandler(CommHandler):

    def __init__(self, target, port):
        CommHandler.__init__(self, target, port)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.disconnect()

    def connect(self):

        self.context = zmq.Context()

        if not self.context:
            raise RuntimeError('Failed to create ZMQ context!')

        self.socket = self.context.socket(zmq.REP)

        if not self.socket:
            raise RuntimeError('Failed to create ZMQ socket!')

        self.socket.bind(self.endpoint)

    def disconnect(self):

        if self.socket:
            self.socket.close()

        if self.context:
            self.context.term()

    def reconnect(self):

        self.disconnect()
        self.connect()

    def recv(self):

        in_msg = self.socket.recv()
        return in_msg
