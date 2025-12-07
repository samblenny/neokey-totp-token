# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: Copyright 2025 Sam Blenny
#
# See NOTES.md for documentation links and pinout info.
#
import atexit
import board
import busio
import collections
import digitalio
import displayio
from fourwire import FourWire
import gc
from micropython import const
from pwmio import PWMOut
import os
import terminalio


from adafruit_24lc32 import EEPROM_I2C
from adafruit_display_text import label
from adafruit_ds3231 import DS3231
from adafruit_neokey.neokey1x4 import NeoKey1x4
from adafruit_st7789 import ST7789

from eeprom_db import check_eeprom_format, is_slot_in_use, load_totp_account
from sb_totp import base32_encode, totp_sha1


# Begin TFT backlight dimming (100% brightness is PWM duty_cycle=0xffff)
BACKLIGHT_ON = const(26214)  # 40% of 0xffff
BACKLIGHT_OFF = const(0)

# NeoKey keypress bitfield constants
NEOKEY0 = const(1)
NEOKEY1 = const(2)
NEOKEY2 = const(4)
NEOKEY3 = const(8)

# NeoKey NeoPixel color constants
NP_AMBER = const(0x202000)
NP_RED   = const(0x400000)
NP_OFF   = const(0)


class Controller:

    def __init__(self):
        # I2C RTC, EEPROM, and NeoKey
        i2c = busio.I2C(board.SCL, board.SDA, frequency=250_000)
        self.i2c = i2c
        self.rtc = DS3231(i2c)
        self.neokey = NeoKey1x4(i2c, addr=0x30)
        self.eeprom = EEPROM_I2C(i2c)
        self.accounts = []                            # TOTP account slot data
        self.selected_acct = None

        # Display backlight PWM dimming
        self.backlight = PWMOut(board.TFT_BACKLIGHT, frequency=500,
            duty_cycle=BACKLIGHT_ON)
        self.bl_enable = True

        # CLUE 240x240 ST7789 TFT Display
        displayio.release_displays()
        gc.collect()
        spi = busio.SPI(board.TFT_SCK, MOSI=board.TFT_MOSI)
        display_bus = FourWire(spi, command=board.TFT_DC,
            chip_select=board.TFT_CS, reset=board.TFT_RESET)
        display = ST7789(display_bus, width=240, height=240, rowstart=80,
            bgr=True, rotation=0, auto_refresh=False)
        group = displayio.Group()
        display.root_group = group
        display.refresh()
        textbox = label.Label(font=terminalio.FONT, scale=4, color=0xefef00)
        textbox.anchor_point = (0, 0)
        textbox.anchored_position = (16, 8)
        textbox.line_spacing = 1.25  # default is 1.25
        group.append(textbox)
        self.spi = spi
        self.display_bus = display_bus
        self.display = display
        self.group = group
        self.textbox = textbox

        # Set an atexit handler to release the display once code.py ends. This
        # is an aesthetic filter to prevent CircuitPython's supervisor from
        # hijacking the display to show noisy stuff I don't want to see.
        def atexit_shutdown_display():
            try:
                self.set_text('OFFLINE')
                displayio.release_displays()
                self.spi.deinit()
                for i in range(4):
                    self.neokey.pixels[i] = 0
            except AttributeError:
                pass
        atexit.register(atexit_shutdown_display)


    def backlight_off(self):
        self.backlight.duty_cycle = BACKLIGHT_OFF
        self.bl_enable = False
        self.set_text('')
        # Also make sure NeoKey NeoPixels are off
        for i in range(4):
            self.set_neokey_off(i)

    def backlight_on(self):
        self.backlight.duty_cycle = BACKLIGHT_ON
        self.bl_enable = True

    def set_text(self, txt):
        self.textbox.text = txt
        self.display.refresh()

    def load_totp_accounts(self):
        # Load TOTP account slot data from EEPROM database
        TOTPAccount = collections.namedtuple("TOTPAccount",
            ["slot", "label", "secret_b32"])
        try:
            eeprom = self.eeprom
            check_eeprom_format(eeprom)
            first_used_index = None
            for slot in range(1, 5):
                if is_slot_in_use(eeprom, slot):
                    label, secret_bytes = load_totp_account(eeprom, slot)
                    secret_b32 = base32_encode(secret_bytes)
                    self.accounts.append(TOTPAccount(slot, label, secret_b32))
                    if first_used_index is None:
                        first_used_index = slot-1
                else:
                    self.accounts.append(None)
            self.selected_acct = first_used_index
        except ValueError as e:
            print(e)

        # Print summary from loading the EEPROM account database
        print('Loaded data for %d TOTP account slots from EEPROM' % (
            len(self.accounts)))
        for i, a in enumerate(self.accounts):
            if a is None:
                print(" slot %d: -- empty --")
            else:
                print(" slot %d: '%s'" % (a.slot, a.label))
        if self.selected_acct is not None:
            print("Selected Slot:", self.accounts[self.selected_acct].slot)

    def select_account(self, slot):
        # Try to select requested slot (return False for empty slot)
        if (0 <= slot < len(self.accounts)):
            self.selected_acct = slot
        else:
            raise ValueError(f"slot number out of range: {slot}")

        if self.accounts[self.selected_acct] is None:
            # This slot is empty
            return False
        # Happy path: slot is configured with an account
        return True

    def get_selected_slot(self):
        return self.selected_acct

    def is_selected_slot_empty(self):
        # Return True if there's no account configured for the selected slot
        return self.accounts[self.selected_acct] is None

    def get_selected_totp(self, unix_time):
        # Get TOTP slot, label, and code for the selected account
        if self.selected_acct is None:
            return ('', '', '')
        acct = self.accounts[self.selected_acct]
        if acct is None:
            return ('', 'empty', '')
        # CAUTION! For boards that use adafruit_hashlib.hashlib for SHA1, this
        # call may take on the order of 2 seconds to finish
        code = totp_sha1(acct.secret_b32, unix_time, digits=6, period=30)
        return (acct.slot, acct.label, code)

    def datetime(self):
        return self.rtc.datetime

    def get_neokey_bits(self):
        # Convert the list of booleans from NeoKey1x4 to an integer bitfield so
        # it's easier to detect state changes using XOR and AND
        k = self.neokey.get_keys()
        return (NEOKEY0 * int(k[0]) + NEOKEY1 * int(k[1]) + NEOKEY2 * int(k[2])
            + NEOKEY3 * int(k[3]))

    def set_neokey(self, key, color):
        self.neokey.pixels[key] = color

    def set_neokey_off(self, key):
        self.neokey.pixels[key] = NP_OFF
