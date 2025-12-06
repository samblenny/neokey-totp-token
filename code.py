# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: Copyright 2025 Sam Blenny
#
# See NOTES.md for documentation links and pinout info.
#
import time

from controller import (Controller, NEOKEY0, NEOKEY1, NEOKEY2, NEOKEY3,
    NP_AMBER, NP_RED)


# The controller manages IO with I2C and SPI devices, and it keeps track of the
# EEPROM-backed database of TOTP accounts
ctrl = Controller()
ctrl.load_totp_accounts()


# These variables help with edge detection of changing values in the main loop
t = ctrl.datetime()
prev_t = t
prev_nk = ctrl.get_neokey_bits()
need_refresh = True

# Main loop
while True:

    # Is it time to update the TOTP code? (period over or refresh requested)
    if t.tm_sec % 30 == 0 or need_refresh:
        # Generate new totp code at multiples of 30 seconds
        unix_time = time.mktime(ctrl.datetime())
        slot, label, totp_code = ctrl.get_selected_totp(unix_time)
        code_changed = True

    # Redraw the display only when backlight is on
    if ctrl.bl_enable:
        seconds = t[5] % 30
        ctrl.set_text('%02d\n%s\n%s' % (seconds, label, totp_code))

    # Make sure the right NeoKey selected slot LED is lit
    for i in range(4):
        if i == ctrl.get_selected_slot():
            if ctrl.is_selected_slot_empty():
                ctrl.set_neokey(i, NP_RED)    # red for empty slots
            else:
                ctrl.set_neokey(i, NP_AMBER)  # amber for configured slots
        else:
            ctrl.set_neokey_off(i)

    # Clear the manual update requested flag
    need_refresh = False

    # Wait until second rolls over
    while t.tm_sec == prev_t.tm_sec and not need_refresh:

        # 1. Spend about 100ms polling for input events
        t_10Hz = time.monotonic() + 0.1
        while time.monotonic() < t_10Hz and not need_refresh:

            # Sleep to rate limit I2C, debounce buttons, do VM background tasks
            time.sleep(0.01)

            # Check the currently selected TOTP account slot
            slot = ctrl.get_selected_slot()

            # Monitor the 4 NeoKeys for state transitions
            nk = ctrl.get_neokey_bits()  # get bitfield of current key states
            diff = nk ^ prev_nk          # use XOR to find which bits changed
            prev_nk = nk                 # update previous values for next loop

            # Did any key get pressed while backlight was off?
            just_woke_up = False
            if (diff & nk) and not ctrl.bl_enable:
                # Rising edge of key press -> wake up the screen
                ctrl.backlight_on()
                need_refresh = True
                just_woke_up = True  # don't go back to sleep when key released

            # Check for key press to select TOTP account slots. When the
            # currently selected key is pressed, turn off the display. When a
            # different key is pressed, try to select the slot for that key.
            for i, bitmask in enumerate((NEOKEY0, NEOKEY1, NEOKEY2, NEOKEY3)):
                if diff & bitmask:
                    if nk & bitmask:
                        # Pressed: Turn off display if this slot is already
                        # selected, or select this slot if not currently
                        # selected
                        if ctrl.bl_enable and slot == i:
                            if not just_woke_up:
                                ctrl.backlight_off()
                                ctrl.set_neokey_off(i)
                        elif ctrl.select_account(i):
                            # Slot is configured
                            ctrl.set_neokey(i, NP_AMBER)
                            need_refresh = True
                        else:
                            # Slot is empty
                            ctrl.set_neokey(i, NP_RED)
                            need_refresh = True
                    else:
                        # Released
                        pass

            # -- END of input polling loop --


        # 2. Poll RTC at about 10 Hz to detect when seconds have changed
        if ctrl.bl_enable:
            t = ctrl.datetime()

    # After the seconds have changed, update the previous time
    prev_t = t
