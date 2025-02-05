import time
import datetime
import random
import os
import sys
import struct
import asyncio

import numpy as np

from logger import app_logger
from .sensor import Sensor
from .ant import ant_device_heartrate
from .ant import ant_device_speed_cadence
from .ant import ant_device_power
from .ant import ant_device_light
from .ant import ant_device_ctrl
from .ant import ant_device_temperature
from .ant import ant_device_multiscan
from .ant import ant_device_search

# ANT+
_SENSOR_ANT = False

_sys_stdout = sys.stdout
f = open(os.devnull, "w")
sys.stdout = f
try:
    from ant.easy.node import Node
    from ant.base.driver import find_driver, DriverNotFound

    # device test
    _driver = find_driver()
    _SENSOR_ANT = True
except ImportError:
    pass
except DriverNotFound:
    pass

f.close()
sys.stdout = _sys_stdout

if _SENSOR_ANT:
    app_logger.info("ANT")


class SensorANT(Sensor):
    # for openant
    node = None
    NETWORK_KEY = [0xB9, 0xA5, 0x21, 0xFB, 0xBD, 0x72, 0xC3, 0x45]
    NETWORK_NUM = 0x00
    CHANNEL = 0x00
    DEVICE_ALL = 0
    scanner = None
    device = {}

    def sensor_init(self):
        global _SENSOR_ANT
        if self.config.G_ANT["STATUS"] and not _SENSOR_ANT:
            self.config.G_ANT["STATUS"] = False

        if self.config.G_ANT["STATUS"]:
            self.node = Node()
            self.node.set_network_key(self.NETWORK_NUM, self.NETWORK_KEY)

        # initialize scan channel (reserve ch0)
        if _SENSOR_ANT:
            app_logger.info("detected ANT+ sensors")
        self.scanner = ant_device_multiscan.ANT_Device_MultiScan(self.node, self.config)
        self.searcher = ant_device_search.ANT_Device_Search(
            self.node, self.config, self.values
        )
        self.scanner.set_main_ant_device(self.device)

        # auto connect ANT+ sensor from setting.conf
        if self.config.G_ANT["STATUS"] and not self.config.G_DUMMY_OUTPUT:
            for key in self.config.G_ANT["ID"].keys():
                if self.config.G_ANT["USE"][key]:
                    antID = self.config.G_ANT["ID"][key]
                    antType = self.config.G_ANT["TYPE"][key]
                    self.connect_ant_sensor(key, antID, antType, False)
            return
        # otherwise, initialize
        else:
            for key in self.config.G_ANT["ID"].keys():
                self.config.G_ANT["USE"][key] = False
                self.config.G_ANT["ID"][key] = 0
                self.config.G_ANT["TYPE"][key] = 0

        # for dummy output
        if not self.config.G_ANT["STATUS"] and self.config.G_DUMMY_OUTPUT:
            # need to set dummy ANT+ device id 0
            self.config.G_ANT["USE"] = {
                "HR": True,
                "SPD": True,
                "CDC": True,  # same as SPD
                "PWR": True,
                "TEMP": False,
            }
            self.config.G_ANT["ID_TYPE"] = {
                "HR": struct.pack("<HB", 0, 0x78),
                "SPD": struct.pack("<HB", 0, 0x79),
                "CDC": struct.pack("<HB", 0, 0x79),  # same as SPD
                "PWR": struct.pack("<HB", 0, 0x0B),
            }
            self.config.G_ANT["TYPE"] = {
                "HR": 0x78,
                "SPD": 0x79,
                "CDC": 0x79,  # same as SPD
                "PWR": 0x0B,
            }
            ac = self.config.G_ANT["ID_TYPE"]
            self.values[ac["HR"]] = {}
            self.values[ac["SPD"]] = {"distance": 0}
            self.values[ac["PWR"]] = {}
            for key in [0x10, 0x11, 0x12]:
                self.values[ac["PWR"]][key] = {"accumulated_power": 0}

        self.reset()

    def start_coroutine(self):
        asyncio.create_task(self.start())

    async def start(self):
        if self.config.G_ANT["STATUS"]:
            await asyncio.get_running_loop().run_in_executor(None, self.node.start)

    def update(self):
        if self.config.G_ANT["STATUS"] or not self.config.G_DUMMY_OUTPUT:
            return

        hr_value = random.randint(70, 150)
        speed_value = random.randint(5, 30) / 3.6  # 5 - 30km/h [unit:m/s]
        cad_value = random.randint(60, 100)
        power_value = random.randint(0, 250)
        timestamp = datetime.datetime.now()

        ac = self.config.G_ANT["ID_TYPE"]
        self.values[ac["HR"]]["heart_rate"] = hr_value
        self.values[ac["SPD"]]["speed"] = speed_value
        self.values[ac["CDC"]]["cadence"] = cad_value
        self.values[ac["PWR"]][0x10]["power"] = power_value

        # TIMESTAMP
        self.values[ac["HR"]]["timestamp"] = timestamp
        self.values[ac["SPD"]]["timestamp"] = timestamp
        self.values[ac["PWR"]][0x10]["timestamp"] = timestamp
        # DISTANCE, TOTAL_WORK
        if self.config.G_MANUAL_STATUS == "START":
            # DISTANCE: unit: m
            if not np.isnan(self.values[ac["SPD"]]["speed"]):
                self.values[ac["SPD"]]["distance"] += (
                    self.values[ac["SPD"]]["speed"] * self.config.G_SENSOR_INTERVAL
                )
            # TOTAL_WORK: unit: j
            if not np.isnan(self.values[ac["PWR"]][0x10]["power"]):
                self.values[ac["PWR"]][0x10]["accumulated_power"] += (
                    self.values[ac["PWR"]][0x10]["power"]
                    * self.config.G_SENSOR_INTERVAL
                )

    def reset(self):
        for dv in self.device.values():
            dv.reset_value()

    def quit(self):
        if not self.config.G_ANT["STATUS"]:
            return
        self.searcher.set_wait_quick_mode()
        # stop scanner and searcher
        if not self.scanner.stop():
            for dv in self.device.values():
                dv.ant_state = "quit"
                dv.disconnect(isCheck=True, isChange=False, wait=0)  # USE: True -> True
            self.searcher.stop_search(resetWait=False)
        self.node.stop()

    def connect_ant_sensor(self, antName, antID, antType, connectStatus):
        if not self.config.G_ANT["STATUS"]:
            return
        self.config.G_ANT["ID"][antName] = antID
        self.config.G_ANT["TYPE"][antName] = antType
        self.config.G_ANT["ID_TYPE"][antName] = struct.pack("<HB", antID, antType)
        antIDType = self.config.G_ANT["ID_TYPE"][antName]
        self.searcher.stop_search(resetWait=False)

        self.config.G_ANT["USE"][antName] = True

        self.searcher.set_wait_normal_mode()

        # existing connection
        if connectStatus:
            return

        # reconnect
        if antIDType in self.device:
            self.device[antIDType].connect(
                isCheck=False, isChange=False
            )  # USE: True -> True)
            self.device[antIDType].ant_state = "connect_ant_sensor"
            self.device[antIDType].init_after_connect()
            return

        # newly connect
        self.values[antIDType] = {}
        if antType == 0x78:
            self.device[antIDType] = ant_device_heartrate.ANT_Device_HeartRate(
                self.node, self.config, self.values[antIDType], antName
            )
        elif antType == 0x79:
            self.device[antIDType] = ant_device_speed_cadence.ANT_Device_Speed_Cadence(
                self.node, self.config, self.values[antIDType], antName
            )
        elif antType == 0x7A:
            self.device[antIDType] = ant_device_speed_cadence.ANT_Device_Cadence(
                self.node, self.config, self.values[antIDType], antName
            )
        elif antType == 0x7B:
            self.device[antIDType] = ant_device_speed_cadence.ANT_Device_Speed(
                self.node, self.config, self.values[antIDType], antName
            )
        elif antType == 0x0B:
            self.device[antIDType] = ant_device_power.ANT_Device_Power(
                self.node, self.config, self.values[antIDType], antName
            )
        elif antType == 0x23:
            self.device[antIDType] = ant_device_light.ANT_Device_Light(
                self.node, self.config, self.values[antIDType], antName
            )
        elif antType == 0x10:
            self.device[antIDType] = ant_device_ctrl.ANT_Device_CTRL(
                self.node, self.config, self.values[antIDType], antName
            )
        elif antType == 0x19:
            self.device[antIDType] = ant_device_temperature.ANT_Device_Temperature(
                self.node, self.config, self.values[antIDType], antName
            )
        self.device[antIDType].ant_state = "connect_ant_sensor"
        self.device[antIDType].init_after_connect()

    def disconnect_ant_sensor(self, antName):
        antIDType = self.config.G_ANT["ID_TYPE"][antName]
        antNames = []
        for k, v in self.config.G_ANT["USE"].items():
            if v and k in self.config.G_ANT["ID_TYPE"]:
                if self.config.G_ANT["ID_TYPE"][k] == antIDType:
                    antNames.append(k)
        for k in antNames:
            # USE: True -> False
            self.device[
                self.config.G_ANT["ID_TYPE"][k]
            ].ant_state = "disconnect_ant_sensor"
            self.device[self.config.G_ANT["ID_TYPE"][k]].disconnect(
                isCheck=True, isChange=True
            )
            self.config.G_ANT["ID_TYPE"][k] = 0
            self.config.G_ANT["ID"][k] = 0
            self.config.G_ANT["TYPE"][k] = 0
            self.config.G_ANT["USE"][k] = False

    def continuous_scan(self):
        if not self.config.G_ANT["STATUS"]:
            return
        self.scanner.set_wait_quick_mode()
        for dv in self.device.values():
            dv.ant_state = "continuous_scan"
            dv.disconnect(isCheck=True, isChange=False, wait=0.5)  # USE: True -> True
        time.sleep(0.5)
        self.scanner.set_wait_scan_mode()
        self.scanner.scan()

    def stop_continuous_scan(self):
        self.scanner.set_wait_quick_mode()
        self.scanner.stop_scan()
        antIDTypes = []
        for k, v in self.config.G_ANT["USE"].items():
            if v and not self.config.G_ANT["ID_TYPE"][k] in antIDTypes:
                antIDTypes.append(self.config.G_ANT["ID_TYPE"][k])
        for antIDType in antIDTypes:
            self.device[antIDType].connect(
                isCheck=True, isChange=False
            )  # USE: True -> True
        self.scanner.set_wait_normal_mode()

    def set_light_mode(self, mode, auto=False):
        if "LGT" not in self.config.G_ANT["USE"] or not self.config.G_ANT["USE"]["LGT"]:
            return
        if mode == "OFF":
            self.device[
                self.config.G_ANT["ID_TYPE"]["LGT"]
            ].send_light_setting_light_off(auto)
        elif mode == "FLASH_LOW":
            self.device[
                self.config.G_ANT["ID_TYPE"]["LGT"]
            ].send_light_setting_flash_low(auto)
        elif mode == "FLASH_HIGH":
            self.device[
                self.config.G_ANT["ID_TYPE"]["LGT"]
            ].send_light_setting_flash_high(auto)
        elif mode == "STEADY_HIGH":
            self.device[
                self.config.G_ANT["ID_TYPE"]["LGT"]
            ].send_light_setting_steady_high(auto)
        elif mode == "STEADY_MID":
            self.device[
                self.config.G_ANT["ID_TYPE"]["LGT"]
            ].send_light_setting_steady_mid(auto)
        elif mode == "ON_OFF_FLASH_LOW":
            self.device[
                self.config.G_ANT["ID_TYPE"]["LGT"]
            ].send_light_setting_light_off_flash_low(auto)
