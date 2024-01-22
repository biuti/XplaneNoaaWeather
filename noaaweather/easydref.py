"""
X-plane NOAA GFS weather plugin.
Copyright (C) 2011-2020 Joan Perez i Cauhe
Copyright (C) 2021-2023 Antonio Golfari
---
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or any later version.
"""

from . import xp

class EasyDref:
    """
    Easy Dataref access

    Copyright (C) 2011-2020 Joan Perez i Cauhe
    Copyright (C) 2021-2023 Antonio Golfari
    """

    datarefs = []
    plugin = False

    def __init__(self, dataref, type="float", register=False, writable=False, default_value=False):
        # Clear dataref
        dataref = dataref.strip()

        self.is_array, dref = False, False
        self.register = register
        self.default_value = default_value

        if ('"' in dataref):
            dref = dataref.split('"')[1]
            dataref = dataref[dataref.rfind('"') + 1:]

        if ('(' in dataref):
            # Detect embedded type, and strip it from dataref
            type = dataref[dataref.find('(') + 1:dataref.find(')')]
            dataref = dataref[:dataref.find('(')] + dataref[dataref.find(')') + 1:]

        if ('[' in dataref):
            # We have an array
            self.is_array = True
            range = dataref[dataref.find('[') + 1:dataref.find(']')].split(':')
            dataref = dataref[:dataref.find('[')]
            if (len(range) < 2):
                range.append(range[0])

            self.initArrayDref(range[0], range[1], type)

        elif (type == "int"):
            self.dr_get = xp.getDatai
            self.dr_set = xp.setDatai
            self.dr_type = xp.Type_Int
            self.cast = int
        elif (type == "float"):
            self.dr_get = xp.getDataf
            self.dr_set = xp.setDataf
            self.dr_type = xp.Type_Float
            self.cast = float
        elif (type == "double"):
            self.dr_get = xp.getDatad
            self.dr_set = xp.setDatad
            self.dr_type = xp.Type_Double
            self.cast = float
        else:
            print(f"ERROR: invalid DataRef type: {type}")

        if dref: dataref = dref

        self.dataref = dataref

        if register:
            self.setCB, self.getCB = False, False
            self.rsetCB, self.rgetCB = False, False

            if self.is_array:
                if writable: self.rsetCB = self.set_cb
                self.rgetCB = self.rget_cb
            else:
                if writable: self.setCB = self.set_cb
                self.getCB = self.get_cb

            self.DataRef = xp.unregisterDataAccessor(
                dataref, self.dr_type,
                writable,
                self.getCB, self.setCB,
                self.getCB, self.setCB,
                self.getCB, self.setCB,
                self.rgetCB, self.rsetCB,
                self.rgetCB, self.rsetCB,
                self.rgetCB, self.rsetCB,
                0, 0
            )

            self.__class__.datarefs.append(self)

            # Local shortcut
            self.set = self.set_f
            self.get = self.get_f

            # Init default value
            if self.is_array:
                self.value_f = [self.cast(0)] * self.index
                self.set = self.rset_f
            else:
                self.value_f = self.cast(0)

        else:
            self.DataRef = xp.findDataRef(dataref)
            if not self.DataRef:
                xp.log(f"Can't find {dataref} DataRef")

    def initArrayDref(self, first, last, type):
        if self.register:
            self.index = 0
            self.count = int(first)
        else:
            self.index = int(first)
            self.count = int(last) - int(first) + 1
            self.last = int(last)

        if (type == "int"):
            self.rget = xp.getDatavi
            self.rset = xp.setDatavi
            self.dr_type = xp.Type_IntArray
            self.cast = int
        elif (type == "float"):
            self.rget = xp.getDatavf
            self.rset = xp.setDatavf
            self.dr_type = xp.Type_FloatArray
            self.cast = float
        elif (type == "bit"):
            self.rget = xp.getDatab
            self.rset = xp.setDatab
            self.dr_type = xp.Type_DataArray
            self.cast = float
        else:
            print(f"ERROR: invalid DataRef type: {type}")
        pass

    def set(self, value):
        if self.is_array:
            self.rset(self.DataRef, value, self.index, len(value))
        else:
            self.dr_set(self.DataRef, self.cast(value))

    def get(self):
        if self.is_array:
            list = []
            self.rget(self.DataRef, list, self.index, self.count)
            return list
        else:
            return self.dr_get(self.DataRef)

    # Local shortcuts
    def set_f(self, value):
        self.value_f = value

    def get_f(self):
        if self.is_array:
            vals = []
            for item in self.value_f:
                vals.append(self.cast(item))
            return vals
        else:
            return self.value_f

    def rset_f(self, value):

        vlen = len(value)
        if vlen < self.count:
            self.value_f = [self.cast(0)] * self.count
            self.value_f = value + self.value[vlen:]
        else:
            self.value_f = value

    # Data access SDK Callbacks
    def set_cb(self, inRefcon, value):
        self.value_f = value

    def get_cb(self, inRefcon):
        return self.cast(self.value_f)

    def rget_cb(self, inRefcon, values, index, limit):
        if values == None:
            return self.count
        else:
            i = 0
            for item in self.value_f:
                if i < limit:
                    values.append(self.cast(item))
                    i += 1
                else:
                    break
            return i

    def rset_cb(self, inRefcon, values, index, count):
        if self.count >= index + count:
            self.value_f = self.value_f[:index] + values + self.value_f[index + count:]
        else:
            return False

    def __getattr__(self, name):
        if name == 'value':
            try:
                return self.get()
            except (SystemError, TypeError) as e:
                print(f"Error trying to retrieve value from {self.dataref}: {e}")
        else:
            raise AttributeError

    def __setattr__(self, name, value):
        if name == 'value':
            self.set(value)
        else:
            self.__dict__[name] = value

    def change_if_diff(self, value) -> bool:
        if self.value != value:
            self.set(value)
            return True
        return False

    def set_default(self):
        if self.default_value and self.value != self.default_value:
            self.set(self.default_value)

    @classmethod
    def cleanup(cls):
        for dataref in cls.datarefs:
            xp.unregisterDataAccessor(dataref.DataRef)

    @classmethod
    def DataRefEditorRegister(cls):
        MSG_ADD_DATAREF = 0x01000000
        PluginID = xp.findPluginBySignature("xplanesdk.examples.DataRefEditor")

        drefs = 0
        if PluginID != xp.NO_PLUGIN_ID:
            for dataref in cls.datarefs:
                xp.sendMessageToPlugin(PluginID, MSG_ADD_DATAREF, dataref.dataref)
                drefs += 1

        return drefs


class EasyCommand:
    """
    Creates a command with an assigned callback with arguments
    """

    def __init__(self, plugin, command, function, args=False, description=''):
        command = f"xjpc/XPNoaaWeather/{command}"
        self.command = xp.createCommand(command, description)
        self.commandCH = self.commandCHandler
        xp.registerCommandHandler(self.command, self.commandCH, 1, 0)

        self.function = function
        self.args = args
        self.plugin = plugin
        # Command handlers

    def commandCHandler(self, inCommand, inPhase, inRefcon):
        if inPhase == 0:
            if self.args:
                if type(self.args).__name__ == 'tuple':
                    self.function(*self.args)
                else:
                    self.function(self.args)
            else:
                self.function()
        return 0

    def destroy(self):
        xp.unregisterCommandHandler(self.command, self.commandCH, 1, 0)
