#!/usr/bin/env python3


from __future__ import annotations
import sys
import json
import argparse
import traceback
import time
import os

__author__ = 'ColorsWind'
__version__ = '1.0.0'

PROTOCOL = 0
RECV_Q = 1
SEND_Q = 2
LOCAL_ADDRESS = 3
FOREIGN_ADDRESS = 4
STATE = 5
PID_PROGRAM = 6
ITEM_LENGTH = 7


def test_admin_privilege() -> bool:
    return os.system("net.exe session > /dev/null") == 0


def cleanup():
    os.system("netsh.exe interface portproxy reset ipv4> /dev/null")
    os.system("netsh.exe advfirewall firewall del rule name='WSL Auto Fowards' > /dev/null")
    print("Successfully clean all netsh port forwarding rules and relevant firewall rules")


class ForwardingManager(object):
    def __init__(self, windows_ip: str, wsl_ip: str, ignore_exception: bool, allow_program_name: list[str],
                 disallow_program_name: list[str]):
        self.forwarding_ports = {}
        self.update_count = 0
        self.windows_ip = windows_ip
        self.wsl_ip = wsl_ip
        self.ignore_exception = ignore_exception
        self.allow_program_name = set(allow_program_name)
        self.disallow_program_name = set(disallow_program_name)

    def should_forward_port(self, program_name: int) -> bool:
        if program_name in self.allow_program_name:
            return True
        if program_name in self.disallow_program_name:
            return False
        return len(self.allow_program_name) == 0

    def get_wsl_bind_ports(self) -> dict[int, tuple[int, str]]:
        with os.popen("netstat -tpln 2> /dev/null") as p:
            query_out = p.readlines()
        ports = {}
        for line in query_out:
            try:
                items = line.split()
                if len(items) != ITEM_LENGTH or items[PROTOCOL] != "tcp":
                    continue
                local_address, local_port = items[LOCAL_ADDRESS].split(":")
                foreign_address, _ = items[FOREIGN_ADDRESS].split(":")
                if items[PID_PROGRAM] == "-":
                    continue
                pid, program_name = items[PID_PROGRAM].split("/")
                if self.should_forward_port(program_name):
                    ports[int(local_port)] = (int(pid), program_name)
            except Exception as e:
                print("Exception occurs while parsing line: ", line, file=sys.stderr)
                print(e, file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                input("Press Enter to continue...")
        return ports

    def start_forwarding_port(self, port: int):
        os.system(f"netsh.exe interface portproxy add v4tov4 "
                  f"listenaddress={self.windows_ip} "
                  f"listenport={port} "
                  f"connectaddress={self.wsl_ip} "
                  f"protocol=tcp > /dev/null")
        os.system(f"netsh.exe advfirewall firewall add rule "
                  f"name='WSL Auto Fowards' "
                  f"dir=in "
                  f"action=allow "
                  f"protocol=TCP "
                  f"localport={port} > /dev/null")

    def stop_forwarding_port(self, port: int):
        os.system(f"netsh.exe interface portproxy delete v4tov4 "
                  f"protocol=TCP "
                  f"listenaddress={self.windows_ip} "
                  f"listenport={port} > /dev/null")
        os.system(f"netsh.exe advfirewall firewall del rule "
                  f"name='WSL Auto Fowards' "
                  f"dir=in action=allow "
                  f"protocol=TCP "
                  f"localport={port} > /dev/null")

    def update_ports(self, curr_ports: dict[int, tuple[int, str]]) -> int:
        update_count = 0
        need_to_remove = self.forwarding_ports
        for port, _ in curr_ports.items():
            if port in need_to_remove:
                # already forwarding but do not need anymore
                del need_to_remove[port]
            else:
                # need to forward
                self.start_forwarding_port(port)
                update_count += 1
        # remove all unecessary port
        for port, _ in need_to_remove.items():
            self.stop_forwarding_port(port)
            update_count += 1
        self.forwarding_ports = curr_ports
        self.update_count += update_count
        return update_count

    def remove_all_ports(self):
        for port in self.forwarding_ports.keys():
            self.stop_forwarding_port(port)
            self.update_count += 1
        self.forwarding_ports.clear()

    def update(self, force_update=False, additional_message=""):
        ports = self.get_wsl_bind_ports()
        update_count = self.update_ports(ports)
        if update_count > 0 or force_update:
            self.update_console()
            if len(additional_message) > 0:
                print(additional_message)

    def update_console(self):
        os.system("clear")
        format_time = time.strftime("%H:%M:%S", time.localtime())
        forwarding_ports = str(len(self.forwarding_ports)).ljust(5, ' ')
        update_count = str(self.update_count).ljust(7, ' ')
        print(
            f"""
+-----------------------------------------------------------------------------+
| WSL-Port-Forward 1.0.0:                                                     |
|                                                                             |
|  Last Update: {format_time}     Fowarding ports: {forwarding_ports}     Update Count: {update_count} |
|                                                                             |
+-----------------------------------------------------------------------------+
| Forwarding ports:                                                           |
|  PID                 Program Name                            Port           |
|=============================================================================|
        """.strip())
        if len(self.forwarding_ports) == 0:
            print("|  No forwading ports found.                                                  |")
        else:
            for port, (pid, program_name) in self.forwarding_ports.items():
                pid_s = str(pid).ljust(6, ' ')
                program_name_s = program_name.ljust(36, ' ')
                port_s = str(port).ljust(5, ' ')
                print(f"|  {pid_s}              {program_name_s}    {port_s}          |")
        print("+-----------------------------------------------------------------------------+")
        print("Press Control + C to exit.")


def main_manual_mode(manager: ForwardingManager):
    manager.update(force_update=True, additional_message="Press any key to update.")
    while True:
        try:
            input()
            manager.update(force_update=True, additional_message="Press any key to update.")
            print("Press Enter to update.")
        except KeyboardInterrupt:
            break


def main_auto_mode(manager: ForwardingManager, interval: float):
    manager.update(force_update=True,
                   additional_message=f"Update only when network state changes (interval={interval}).")
    while True:
        try:
            time.sleep(interval)
            manager.update(additional_message=f"Update only when network state changes (interval={interval}).")
        except KeyboardInterrupt:
            break


def load_config():
    config_path = os.path.join(os.environ['HOME'], '.wsl-port-forward.json')
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf8") as f:
            config = json.load(f)
        print("Successfully load config.")
    else:
        config = {}
    config.setdefault('update_interval', 0.5)
    config.setdefault('windows_ip', '0.0.0.0')
    with os.popen("hostname -I | awk '{print $1}'") as p:
        config.setdefault('wsl_ip', p.read().strip())
    config.setdefault('ignore_exception', False)
    config.setdefault('allow_program_name', list())
    config.setdefault('disallow_program_name', list())
    return config


def save_config(config: dict):
    config_path = os.path.join(os.environ['HOME'], '.wsl-port-forward.json')
    with open(config_path, "w", encoding="utf8") as f:
        json.dump(config, f, indent=4)
    print("Successfully save config.")


def main():
    config = load_config()
    parser = argparse.ArgumentParser(
        description="A script that enable outside program access WSL2 ports by port forwarding.")
    parser.add_argument('--mode', type=str, default='auto', choices=['auto', 'manual'],
                        help='when to update port forwarding rule.')
    parser.add_argument('--interval', type=float, default=config['update_interval'],
                        help='how often are network state changes detected (auto mode only).')
    parser.add_argument('--allow', action='append', default=config['allow_program_name'],
                        help='program name that allows external access to the port')
    parser.add_argument('--disallow', action='append', default=config['disallow_program_name'],
                        help='program name that disallows external access to the port')
    parser.add_argument('--windows_ip', type=str, default=config['windows_ip'],
                        help='windows ip that external can access')
    parser.add_argument('--wsl_ip', type=str, default=config['wsl_ip'],
                        help='wsl ip that windows can access')
    parser.add_argument('--ignore_exception', action='store_true', default=config['ignore_exception'],
                        help='whether interpret script when exception occurs')
    parser.add_argument('--gen_config', action='store_true', default=False,
                        help='generate config in ~/.wsl_port_forwarding.json')
    parser.add_argument('--clean_rules', action='store_true', default=False,
                        help='clean all netsh port forwarding rule and relevant firewall rules')
    parsed_args = parser.parse_args()
    manager = ForwardingManager(
        windows_ip=parsed_args.windows_ip,
        wsl_ip=parsed_args.wsl_ip,
        ignore_exception=parsed_args.ignore_exception,
        allow_program_name=parsed_args.allow,
        disallow_program_name=parsed_args.disallow,
    )
    if parsed_args.gen_config:
        save_config(config)
        exit(0)
    if not test_admin_privilege():
        print("You need Windows administrator privileges to use this script.")
        exit(-1)
    if parsed_args.clean_rules:
        cleanup()
        exit(0)
    if parsed_args.mode == 'auto':
        main_auto_mode(manager, parsed_args.interval)
    else:
        main_manual_mode(manager)
    print("\nExits, removing ports forwarding and firewall rules.")
    manager.remove_all_ports()
    print("\nRemoving completed!")


if __name__ == "__main__":
    main()
