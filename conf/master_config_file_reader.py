#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright 2021 Gabriele Iannetti <g.iannetti@gsi.de>
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


import configparser
import os

from conf.config_value_error import ConfigValueError


class MasterConfigFileReader:

    def __init__(self, config_file):

        if not os.path.isfile(config_file):
            raise IOError(f"The config file does not exist or is not a file: {config_file}")

        config = configparser.ConfigParser()
        config.read(config_file)

        self.pid_file = config.get('control', 'pid_file')

        self.controller_timeout = config.getfloat('control', 'controller_timeout')
        self.controller_wait_duration = config.getint('control', 'controller_wait_duration')
        self.task_resend_timeout = config.getint('control', 'task_resend_timeout')

        self.comm_target = config.get('comm', 'target')
        self.comm_port = config.getint('comm', 'port')
        self.poll_timeout = config.getint('comm', 'poll_timeout') * 1000

        self.log_filename = config.get('log', 'filename')

        self.task_gen_module = config.get('task_generator', 'module')
        self.task_gen_class = config.get('task_generator', 'class')
        self.task_gen_config_file = config.get('task_generator', 'config_file')
