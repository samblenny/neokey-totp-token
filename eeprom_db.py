# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: Copyright 2025 Sam Blenny
#
# Helper routines for managing a 4KB 24LC32 I2C EEPROM as a database file for
# TOTP account info. The first 32 bytes (1 EEPROM page) are a header.
#
# Database file format:
# - The first 4 bytes (eeprom[0:4]) of a formatted EEPROM should be b'TOTP'
# - Next 4 bytes (eeprom[4:8]) of the header are markers to indicate which
#   account slots are in use
# - Remainder of header (eeprom[8:32]) is reserved
# - Account slots are 64 bytes. Slot 1 is at eeprom[32:96], slot 2 is at
#   eeprom[96:160], etc.
# - Account slot format is: 8 bytes null padded label, 24 bytes reserved, 32
#   bytes null filled TOTP secret (stored as raw bytes, *not* as base32 string)
#


def check_eeprom_format(eeprom):
    # Checks if the EEPROM is formatted with 'TOTP' magic bytes
    if eeprom[0:4] != b'TOTP':
        raise ValueError("EEPROM not formatted. Try util.format_eeprom().")


def is_slot_in_use(eeprom, slot):
    # Checks if the slot is in use by checking the in-use marker
    in_use_marker = 4 + (slot - 1)
    return eeprom[in_use_marker] == b'\xFF'


def load_totp_account(eeprom, slot):
    # Ensure slot number is valid and slot is in use
    if not (1 <= slot <= 4):
        raise ValueError("Invalid slot number. Must be between 1 and 4.")
    if not is_slot_in_use(eeprom, slot):
        raise ValueError(f"Slot {slot} is not in use.")

    # Define the base address for the slot
    base = 32 + (slot - 1) * 64

    # Load the label (trim nulls) and secret bytes
    label = eeprom[base:base + 8].decode('utf-8').rstrip('\x00')
    secret_bytes = eeprom[base + 32:base + 64]

    return label, secret_bytes
