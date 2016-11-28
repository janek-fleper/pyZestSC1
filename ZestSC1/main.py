import sys
import os
import struct
import array
import logging

import numpy as np
import usb.core
import usb.util

ID_VENDOR = 0x165d
ID_PRODUCT = 0x0001

BITFILE = {'name': 0x61, 'part': 0x62, 'date': 0x63, 'time': 0x64,
            'image': 0x65}

REQUEST = {'write_register': 0xd0, 'read_register': 0xd1,
           'start_config': 0xd2, 'config_status': 0xd3,
           'weeprom_write': 0xd7, 'eeprom_read': 0xd8,
           'firmware': 0xdc, 'reset_8051': 0xa0}

EEPROM = {'fpga_type': 0xfffa, 'card_id': 0xfffb,
            'serial_number': 0xfffc, 'memory_size': 0xfff6}

ENDPOINT = {'ctrl_write': 0x0040, 'ctrl_read': 0x00c0,
            'data_write': 0x0002, 'data_read': 0x0086,
            'int_read': 0x0081}

VALUE_8051 = 0xe600

# convert array [AB, CD, ...]  to ABCD... (in hex)
def byteshift(array):
    shifted_sum = 0
    for i in range(len(array)):
        shifted_sum += array[i] * 2**(8 * (len(array) - 1 - i))

    return shifted_sum

# the length of the section is saved in len_bytes
def read_bitfile_section(f, len_bytes):
    length = [struct.unpack('B', f.read(1))[0] for i in range(len_bytes)]
    length = byteshift(length)
    return length, f.read(length)
    
# 16 bytes per row, similar to wireshark capture
def print_bitfile_to_file(bitfile, length):
    f_out = open('f_out.txt', 'w')
    for i in range(0, length, 16):
        if ((length - i) < 16):
            j_max = length - i
        else:
            j_max = 16
        for j in range(j_max):
            f_out.write('{:02X} '.format(bitfile[i + j]))
        f_out.write('\n')
    f_out.close()

# "while byte:" loops over the bitfile until the end is reached
# struct.unpack converts byte to a readable format
def open_bitfile(path_to_file):
    ret = {}

    with open(path_to_file, mode='rb') as f:
        byte = f.read(1)
        while byte:
            value = struct.unpack('B', byte)[0]

            if value == BITFILE['name']:
                ret['name'] = read_bitfile_section(f, 2)
            if value == BITFILE['part']:
                ret['part'] = read_bitfile_section(f, 2)
            if value == BITFILE['date']:
                ret['date'] = read_bitfile_section(f, 2)
            if value is BITFILE['time']:
                ret['time'] = read_bitfile_section(f, 2)
            if value is BITFILE['image']:
                ret['image'] = read_bitfile_section(f, 4)

            byte = f.read(1)

#        self.print_bitfile_to_file(ret['image'][1], ret['image'][0])
    
    return ret

# weird size modification, no idea why it is necessary
def modify_bitfile_image(bitf):
    image_size = bitf['image'][0]
    length = (image_size + 511 + 512)&~511

    bitarray = [0] * length
    for i in range(image_size):
        bitarray[i] = bitf['image'][1][i]

    return bitarray

class Board:
# device is not None if usb.core.find() does not find any boards
    def __init__(self, device=None):
        self.dev = device

        device.set_configuration()

    def read_eeprom(self, address):
        return self.dev.ctrl_transfer(ENDPOINT['ctrl_read'],
                        REQUEST['read_eeprom'], address, 0, 3, timeout=1000)

    def get_fpga_type(self):
        return self.read_eeprom(EEPROM['fpga_type'])[2]

    def get_card_id(self):
        return self.read_eeprom(EEPROM['card_id'])[2]

    def get_serial_number(self):
        return np.array([self.read_eeprom(EEPROM['serial_number'] + i)[2]
                            for i in range(4)])

    def get_memory_size(self):
        return np.array([self.read_eeprom(EEPROM['memory_size'] + i)[2]
                            for i in range(4)])

    def get_firmware_version(self):
        return np.array(self.dev.ctrl_transfer(ENDPOINT['ctrl_read'],
                        REQUEST['firmware'], 0, 0, 3)[0:3:], timeout=1000)

    def __str__(self):
        print('card_id: {}'.format(self.get_card_id()))
        print('fpga_type: {}'.format(self.get_fpga_type()))
        print('serial_number: {}'.format(self.get_serial_number()))
        print('memory_size: {}'.format(self.get_memory_size()))
        print('firmware_version: {}?'.format(self.get_firmware_version()))

    def reset_8051(self):
        ret = np.array([0, 0])
        ret[0] = self.dev.ctrl_transfer(ENDPOINT['ctrl_write'],
                REQUEST['reset_8051'], VALUE_8051, 0, [1], timeout=1000)
        ret[1] = self.dev.ctrl_transfer(ENDPOINT['ctrl_write'],
                REQUEST['reset_8051'], VALUE_8051, 0, [0], timeout=1000)
#        print('reset_8051: {}'.format(ret))

# Not sure why endpoint is 'ctrl_read' and not 'ctrl_write'
    def write_register(self, index, data):
        ret = self.dev.ctrl_transfer(ENDPOINT['ctrl_read'],
                REQUEST['write_register'], wValue=value, wIndex=index,
                data_or_wLength=data, timeout=1000)
        logging.debug('write_register: {}'.format(ret))

    def read_register(self, value, index, length):
        ret = self.dev.ctrl_transfer(ENDPOINT['ctrl_read'],
                REQUEST['read_register'], wValue=0, wIndex=index,
                data_or_wLength=length, timeout=1000)
        logging.debug('read_register: {}'.format(ret))
        return ret

# Not sure if timeout=1000 is necessary
    def write_data(self, data):
        assert self.dev.write(ENDPOINT['data_write'], data) == len(data)

    def read_data(self, length):
        ret = self.dev.write(ENDPOINT['data_read'], length, timeout=1000)
        logging.debug('read_data: {}'.format(ret))
        return ret

# Not certain if it is really necessary. According to the original driver
# one should send a 4096 byte dummy configuration if the first configuration
# fails. Default should be to use reset_8051() instead of open_card().
    def open_card(self):
        self.reset_8051()

        ret = self.dev.ctrl_transfer(ENDPOINT['ctrl_read'],
                REQUEST['start_config'], wValue=4096, wIndex=4096,
                data_or_wLength=array.array('B', [0, 0]), timeout=1000)
        logging.debug('ctrl_transfer: {}'.format(ret))

        Buffer = np.full(4096, 0, dtype=np.uint16)
        Buffer = array.array('B', Buffer)

        ret = self.dev.write(ENDPOINT['data_write'], Buffer, timeout=1000)
        logging.debug('bulk_write: {}'.format(ret))

        self.reset_8051()

    def load_bitarray_to_board(self, bitarray):
        self.reset_8051()

        length = len(bitarray)
        wValue = (length>>16)&0xffff
        wIndex = length&0xffff
        ret = self.dev.ctrl_transfer(ENDPOINT['ctrl_read'],
                REQUEST['start_config'], wValue=wValue, wIndex=wIndex,
                data_or_wLength=array.array('B', [0, 0]), timeout=1000)
        logging.debug('ctrl_transfer: {}'.format(ret))

        ret = self.dev.write(ENDPOINT['data_write'], bitarray)
        logging.debug('bulk_write: {}'.format(ret))

        ret = self.dev.ctrl_transfer(ENDPOINT['ctrl_read'],
                REQUEST['config_status'], wValue=0, wIndex=0,
                data_or_wLength=array.array('B', [0, 0, 0]), timeout=1000)
        logging.debug('ctrl_transfer: {}'.format(ret))

    def close_board(self):
        ret = self.dev.ctrl_transfer(ENDPOINT['ctrl_read'],
                REQUEST['start_config'], wValue=4096, wIndex=4096,
                data_or_wLength=array.array('B', [0, 0]), timeout=1000)
        logging.debug('ctrl_transfer: {}'.format(ret))

        self.reset_8051()


# find_all=True: devs is not None if no boards are found, so it's pointless to
# check if any boards were found
# find_all=False: dev is None if no board is found
# the usb backend can be changed if required
def find_boards():
#    backend = usb.backend.libusb1.get_backend(find_library=lambda x:
#                                               "/usr/lib/libusb-1.0.so")
#    devs = usb.core.find(find_all=True, idVendor=VENDOR_ID,
#                            idProduct=PRODUCT_ID, backend=backend)

    devs = usb.core.find(find_all=True, idVendor=ID_VENDOR,
                            idProduct=ID_PRODUCT)

    return [Board(device=dev) for dev in devs]

#def main():
#    boards = find_boards()
#    boards[0].open_card()
#    boards[0].reset_8051()
#    boards[0].load_bitarray_to_board()
#    boards[0].close_board()
#
#if __name__ == '__main__':
#    main()
