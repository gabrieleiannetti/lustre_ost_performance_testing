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


import argparse
import importlib
import logging
import os
import signal
import sys
import time

from comm.master_handler import MasterCommHandler
from conf.master_config_file_reader import MasterConfigFileReader
from ctrl.task_status_item import TaskState
from ctrl.task_status_item import TaskStatusItem
from ctrl.pid_control import PIDControl
from ctrl.shared_queue import SharedQueue
from ctrl.critical_section import CriticalSection
from msg.exit_command import ExitCommand
from msg.message_factory import MessageFactory
from msg.message_type import MessageType
from msg.acknowledge import Acknowledge
from msg.task_assign import TaskAssign
from msg.wait_command import WaitCommand
from version import cyclone
from version.minimal_python import MinimalPython


TASK_DISTRIBUTION = True


def init_arg_parser():

    default_config_file = "/etc/cyclone/master.conf"

    parser = argparse.ArgumentParser(description='Cyclone Master')

    parser.add_argument('-f',
                        '--config-file',
                        dest='config_file',
                        type=str,
                        required=False,
                        help=f"Use this config file (default: {default_config_file})",
                        default=default_config_file)

    parser.add_argument('-D',
                        '--debug',
                        dest='enable_debug',
                        required=False,
                        action='store_true',
                        help='Enable debug log messages')

    parser.add_argument('-v',
                        '--version',
                        dest='print_version',
                        required=False,
                        action='store_true',
                        help='Print version number')

    return parser.parse_args()


def init_logging(log_filename, enable_debug):

    if enable_debug:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO

    if log_filename:
        logging.basicConfig(filename=log_filename, level=log_level, format="%(asctime)s - %(levelname)s: %(message)s")
    else:
        logging.basicConfig(level=log_level, format="%(asctime)s - %(levelname)s: %(message)s")


def stop_task_distribution():

    global TASK_DISTRIBUTION

    if TASK_DISTRIBUTION:
        TASK_DISTRIBUTION = False


def signal_handler(signum, frame):
    # pylint: disable=unused-argument

    if signum == signal.SIGHUP:

        logging.info("Master retrieved hang-up signal.")
        stop_task_distribution()

    if signum == signal.SIGINT:

        logging.info("Master retrieved interrupt program signal.")
        stop_task_distribution()

    if signum == signal.SIGTERM:

        logging.info("Master Retrieved signal to terminate.")
        stop_task_distribution()


def check_all_controller_down(count_active_controller):

    if not count_active_controller:

        logging.info('Shutdown of controllers complete!')
        return True

    logging.debug("Waiting for number of controllers to quit: %i", count_active_controller)
    return False


def create_task_generator(task_queue, result_queue, config_file_reader):

    module_name = config_file_reader.task_gen_module
    class_name = config_file_reader.task_gen_class
    config_file = config_file_reader.task_gen_config_file

    dynamic_module = importlib.import_module(module_name)
    dynamic_class = getattr(dynamic_module, class_name)

    return dynamic_class(task_queue, result_queue, config_file)


def main():

    MinimalPython.check()

    error_count = 0
    max_error_count = 100

    task_generator = None

    try:

        args = init_arg_parser()

        if args.print_version:
            print(f"Version {cyclone.VERSION}")
            sys.exit()

        config_file_reader = MasterConfigFileReader(args.config_file)

        init_logging(config_file_reader.log_filename, args.enable_debug)

        with PIDControl(config_file_reader.pid_file) as pid_control, \
                MasterCommHandler(config_file_reader.comm_target,
                                  config_file_reader.comm_port,
                                  config_file_reader.poll_timeout) as comm_handler, \
                SharedQueue() as task_queue, \
                SharedQueue() as result_queue:

            if pid_control.lock():

                logging.info("Started")
                logging.info(f"Master PID: {pid_control.pid()}")
                logging.info(f"Version: {cyclone.VERSION}")

                signal.signal(signal.SIGHUP, signal_handler)
                signal.signal(signal.SIGINT, signal_handler)
                signal.signal(signal.SIGTERM, signal_handler)

                signal.siginterrupt(signal.SIGHUP, True)
                signal.siginterrupt(signal.SIGINT, True)
                signal.siginterrupt(signal.SIGTERM, True)

                comm_handler.connect()

                controller_heartbeat_dict = dict()
                task_status_dict = dict()

                controller_timeout = config_file_reader.controller_timeout
                controller_wait_duration = config_file_reader.controller_wait_duration
                task_resend_timeout = config_file_reader.task_resend_timeout

                task_generator = create_task_generator(task_queue, result_queue, config_file_reader)
                task_generator.start()

                # TODO: Make a class for the master.
                global TASK_DISTRIBUTION

                run_flag = True

                while run_flag:

                    try:

                        last_exec_timestamp = int(time.time())

                        recv_data = comm_handler.recv_string()

                        send_msg = None

                        if recv_data:

                            logging.debug("Retrieved message: %s", recv_data)

                            recv_msg = MessageFactory.create(recv_data)
                            recv_msg_type = recv_msg.type()

                            # TODO: Caution, sender is not set everywhere!
                            controller_heartbeat_dict[recv_msg.sender] = int(time.time())

                            if TASK_DISTRIBUTION:

                                if recv_msg_type == MessageType.TASK_REQUEST():

                                    task = None

                                    with CriticalSection(task_queue.lock, timeout=1) as critical_section:

                                        if critical_section.is_locked():

                                            if not task_queue.is_empty():
                                                task = task_queue.pop_nowait()

                                            else:

                                                if not task_generator.is_alive():

                                                    TASK_DISTRIBUTION = False
                                                    controller_wait_duration = 0

                                                    # Allow a TaskGenerator to quit itself without notifying the master.
                                                    logging.info("Task Generator is not alive.")

                                    if task:

                                        do_task_assign = False

                                        if task.tid in task_status_dict:

                                            task_resend_threshold = \
                                                (task_status_dict[task.tid].timestamp + task_resend_timeout)

                                            if task_status_dict[task.tid].state == TaskState.finished() \
                                                    or last_exec_timestamp >= task_resend_threshold:

                                                do_task_assign = True

                                            elif task_status_dict[task.tid].state == TaskState.assigned() \
                                                    and last_exec_timestamp < task_resend_threshold:

                                                logging.debug("Ignoring task to assign..."
                                                              " - Waiting for task with TID to finish: %s", task.tid)

                                                send_msg = WaitCommand(controller_wait_duration)

                                            else:
                                                raise RuntimeError(f"Undefined state processing task: {task.tid}")

                                        else:
                                            do_task_assign = True

                                        # TODO: Could be a method to be called instead of `do_task_assign = True`
                                        if do_task_assign:

                                            task_status_dict[task.tid] = \
                                                TaskStatusItem(task.tid,
                                                               TaskState.assigned(),
                                                               recv_msg.sender,
                                                               int(time.time()))

                                            send_msg = TaskAssign(task)

                                    else:
                                        send_msg = WaitCommand(controller_wait_duration)

                                    logging.debug("Sending message: %s", send_msg.to_string())
                                    comm_handler.send_string(send_msg.to_string())

                                elif recv_msg_type == MessageType.TASK_FINISHED():

                                    tid = recv_msg.tid

                                    if tid in task_status_dict:

                                        if recv_msg.sender == task_status_dict[tid].controller:

                                            logging.debug("Retrieved finished message for TID: %s", tid)
                                            task_status_dict[tid].state = TaskState.finished()
                                            task_status_dict[tid].timestamp = int(time.time())

                                            logging.debug("Pushing TID to result queue: %s", tid)
                                            result_queue.push(tid)

                                        else:
                                            logging.warning("Retrieved task finished from different controller!")

                                    else:
                                        raise RuntimeError("Inconsistency detected on task finished!")

                                    send_msg = Acknowledge()

                                    if logging.root.isEnabledFor(logging.DEBUG):
                                        logging.debug("Sending message: %s", send_msg.to_string())

                                    comm_handler.send_string(send_msg.to_string())

                                elif recv_msg_type == MessageType.HEARTBEAT():

                                    send_msg = Acknowledge()

                                    if logging.root.isEnabledFor(logging.DEBUG):
                                        logging.debug("Sending message: %s", send_msg.to_string())

                                    comm_handler.send_string(send_msg.to_string())

                                else:
                                    raise RuntimeError(f"Undefined type found in message: {recv_msg.to_string()}")

                            else:   # Do graceful shutdown, since task distribution is off!

                                send_msg = ExitCommand()

                                if logging.root.isEnabledFor(logging.DEBUG):
                                    logging.debug("Sending message: %s", send_msg.to_string())

                                comm_handler.send_string(send_msg.to_string())  # Does not block.

                                controller_heartbeat_dict.pop(recv_msg.sender, None)

                                if check_all_controller_down(len(controller_heartbeat_dict)):
                                    run_flag = False

                        else:   # POLL-TIMEOUT

                            logging.debug('RECV-MSG TIMEOUT')

                            # This gives controllers the last chance to quit themselves until a timeout is reached.
                            if not TASK_DISTRIBUTION:

                                for controller_name in controller_heartbeat_dict.keys():

                                    controller_threshold = \
                                        controller_heartbeat_dict[controller_name] + controller_timeout

                                    if last_exec_timestamp >= controller_threshold:
                                        controller_heartbeat_dict.pop(controller_name, None)

                                if check_all_controller_down(len(controller_heartbeat_dict)):
                                    run_flag = False

                    except Exception as err:

                        error_count += 1
                        _, _, exc_tb = sys.exc_info()
                        filename = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                        logging.error(f"Caught exception in main loop: {err} - {filename} (line: {exc_tb.tb_lineno})")

                        stop_task_distribution()

                        if error_count == max_error_count:
                            run_flag = False

            else:

                logging.error(f"Another instance might be already running (PID file: {config_file_reader.pid_file})!")
                sys.exit(1)

    except Exception as err:

        error_count += 1
        _, _, exc_tb = sys.exc_info()
        filename = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
        logging.error(f"Caught exception in main block: {err} - {filename} (line: {exc_tb.tb_lineno})")

    try:

        if task_generator and task_generator.is_alive():

            os.kill(task_generator.pid, signal.SIGUSR1)

            for _ in range(0, 10, 1):

                if task_generator.is_alive():
                    logging.debug("Waiting for Task Generator to finish...")
                    time.sleep(1)
                else:
                    break

            if task_generator.is_alive():
                task_generator.terminate()
                task_generator.join()

    except Exception as err:

        error_count += 1
        _, _, exc_tb = sys.exc_info()
        filename = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
        logging.error(f"Exception in {filename} (line: {exc_tb.tb_lineno}): {err}")

    logging.info("Finished")

    if error_count:
        sys.exit(1)

    sys.exit(0)


if __name__ == '__main__':
    main()
