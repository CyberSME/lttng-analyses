#!/usr/bin/env python3
#
# The MIT License (MIT)
#
# Copyright (C) 2015 - Julien Desfossez <jdesfossez@efficios.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import math
import re
import time
import datetime
import socket
import struct
import sys
from linuxautomaton import sv

NSEC_PER_SEC = 1000000000
MSEC_PER_NSEC = 1000000

O_CLOEXEC = 0o2000000


# imported from include/linux/kdev_t.h
def kdev_major_minor(dev):
    MINORBITS = 20
    MINORMASK = ((1 << MINORBITS) - 1)
    major = dev >> MINORBITS
    minor = dev & MINORMASK
    return '(%d,%d)' % (major, minor)


def get_disk(dev, disks):
    if dev not in disks:
        disk = sv.Disk()
        disk.name = '%d' % dev
        dev.prettyname = kdev_major_minor(dev)
        disks[dev] = disks
    else:
        disk = disks[dev]
    return disk


def convert_size(size, padding_after=False, padding_before=False):
    if padding_after and size < 1024:
        space_after = ' '
    else:
        space_after = ''
    if padding_before and size < 1024:
        space_before = ' '
    else:
        space_before = ''
    if size <= 0:
        return '0 ' + space_before + 'B' + space_after
    size_name = ('B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB')
    i = int(math.floor(math.log(size, 1024)))
    p = math.pow(1024, i)
    s = round(size/p, 2)
    if s > 0:
        try:
            v = '%0.02f' % s
            return '%s %s%s%s' % (v, space_before, size_name[i], space_after)
        except:
            print(i, size_name)
            raise Exception('Too big to be true')
    else:
        return '0 B'


def is_multi_day_trace_collection(handles):
    time_begin = None

    for handle in handles.values():
        if time_begin is None:
            time_begin = time.localtime(handle.timestamp_begin / NSEC_PER_SEC)
            year_begin = time_begin.tm_year
            month_begin = time_begin.tm_mon
            day_begin = time_begin.tm_mday

        time_end = time.localtime(handle.timestamp_end / NSEC_PER_SEC)
        year_end = time_end.tm_year
        month_end = time_end.tm_mon
        day_end = time_end.tm_mday

        if year_begin != year_end:
            return True
        elif month_begin != month_end:
            return True
        elif day_begin != day_end:
            return True

    return False


def trace_collection_date(handles):
    if is_multi_day_trace_collection(handles):
        return None

    handle = handles.values[0]
    trace_time = time.localtime(handle.timestamp_begin / NSEC_PER_SEC)
    year = trace_time.tm_year
    month = trace_time.tm_mon
    day = trace_time.tm_mday
    return (year, month, day)


def extract_timerange(handles, timerange, gmt):
    p = re.compile(r'^\[(?P<begin>.*),(?P<end>.*)\]$')
    if not p.match(timerange):
        return None
    begin_str = p.search(timerange).group('begin').strip()
    end_str = p.search(timerange).group('end').strip()
    begin = date_to_epoch_nsec(handles, begin_str, gmt)
    end = date_to_epoch_nsec(handles, end_str, gmt)
    return (begin, end)


def date_to_epoch_nsec(handles, date, gmt):
    # match 2014-12-12 17:29:43.802588035 or 2014-12-12T17:29:43.802588035
    p1 = re.compile(r'^(?P<year>\d\d\d\d)-(?P<mon>[01]\d)-'
                    r'(?P<day>[0123]\d)[\sTt]'
                    r'(?P<hour>\d\d):(?P<min>\d\d):(?P<sec>\d\d).'
                    r'(?P<nsec>\d\d\d\d\d\d\d\d\d)$')
    # match 2014-12-12 17:29:43 or 2014-12-12T17:29:43
    p2 = re.compile(r'^(?P<year>\d\d\d\d)-(?P<mon>[01]\d)-'
                    r'(?P<day>[0123]\d)[\sTt]'
                    r'(?P<hour>\d\d):(?P<min>\d\d):(?P<sec>\d\d)$')
    # match 17:29:43.802588035
    p3 = re.compile(r'^(?P<hour>\d\d):(?P<min>\d\d):(?P<sec>\d\d).'
                    r'(?P<nsec>\d\d\d\d\d\d\d\d\d)$')
    # match 17:29:43
    p4 = re.compile(r'^(?P<hour>\d\d):(?P<min>\d\d):(?P<sec>\d\d)$')

    if p1.match(date):
        year = p1.search(date).group('year')
        month = p1.search(date).group('mon')
        day = p1.search(date).group('day')
        hour = p1.search(date).group('hour')
        minute = p1.search(date).group('min')
        sec = p1.search(date).group('sec')
        nsec = p1.search(date).group('nsec')
    elif p2.match(date):
        year = p2.search(date).group('year')
        month = p2.search(date).group('mon')
        day = p2.search(date).group('day')
        hour = p2.search(date).group('hour')
        minute = p2.search(date).group('min')
        sec = p2.search(date).group('sec')
        nsec = 0
    elif p3.match(date):
        collection_date = trace_collection_date(handles)
        if collection_date is None:
            print("Use the format 'yyyy-mm-dd hh:mm:ss[.nnnnnnnnn]' "
                  "for multi-day traces")
            return None
        (year, month, day) = collection_date
        hour = p3.search(date).group('hour')
        minute = p3.search(date).group('min')
        sec = p3.search(date).group('sec')
        nsec = p3.search(date).group('nsec')
    elif p4.match(date):
        collection_date = trace_collection_date(handles)
        if collection_date is None:
            print("Use the format 'yyyy-mm-dd hh:mm:ss[.nnnnnnnnn]' "
                  "for multi-day traces")
            return None
        (year, month, day) = collection_date
        hour = p4.search(date).group('hour')
        minute = p4.search(date).group('min')
        sec = p4.search(date).group('sec')
        nsec = 0
    else:
        return None

    date_time = datetime.datetime(int(year), int(month), int(day), int(hour),
                          int(minute), int(sec))
    if gmt:
        date_time = date_time + datetime.timedelta(seconds=time.timezone)
    return int(date_time.timestamp()) * NSEC_PER_SEC + int(nsec)


def process_date_args(command):
    command._arg_multi_day = is_multi_day_trace_collection(command._handles)
    if command._arg_timerange:
        (command._arg_begin, command._arg_end) = \
            extract_timerange(command._handles, command._arg_timerange,
                              command._arg_gmt)
        if command._arg_begin is None or command._arg_end is None:
            print('Invalid timeformat')
            sys.exit(1)
    else:
        if command._arg_begin:
            command._arg_begin = date_to_epoch_nsec(command._handles,
                                                    command._arg_begin,
                                                    command._arg_gmt)
            if command._arg_begin is None:
                print('Invalid timeformat')
                sys.exit(1)
        if command._arg_end:
            command._arg_end = date_to_epoch_nsec(command._handles,
                                                  command._arg_end,
                                                  command._arg_gmt)
            if command._arg_end is None:
                print('Invalid timeformat')
                sys.exit(1)


def ns_to_asctime(ns):
    return time.asctime(time.localtime(ns/NSEC_PER_SEC))


def ns_to_hour(ns):
    date = time.localtime(ns / NSEC_PER_SEC)
    return '%02d:%02d:%02d' % (date.tm_hour, date.tm_min, date.tm_sec)


def ns_to_hour_nsec(ns, multi_day=False, gmt=False):
    if gmt:
        date = time.gmtime(ns / NSEC_PER_SEC)
    else:
        date = time.localtime(ns / NSEC_PER_SEC)
    if multi_day:
        return ('%04d-%02d-%02d %02d:%02d:%02date.%09d' %
                (date.tm_year, date.tm_mon, date.tm_mday, date.tm_hour,
                 date.tm_min, date.tm_sec, ns % NSEC_PER_SEC))
    else:
        return ('%02d:%02d:%02date.%09d' %
                (date.tm_hour, date.tm_min, date.tm_sec, ns % NSEC_PER_SEC))


def ns_to_sec(ns):
    return '%lu.%09u' % (ns / NSEC_PER_SEC, ns % NSEC_PER_SEC)


def ns_to_day(ns):
    date = time.localtime(ns/NSEC_PER_SEC)
    return '%04d-%02d-%02d' % (date.tm_year, date.tm_mon, date.tm_mday)


def sec_to_hour(ns):
    date = time.localtime(ns)
    return '%02d:%02d:%02d' % (date.tm_hour, date.tm_min, date.tm_sec)


def sec_to_nsec(sec):
    return sec * NSEC_PER_SEC


def seq_to_ipv4(ip):
    return '{}.{}.{}.{}'.format(ip[0], ip[1], ip[2], ip[3])


def int_to_ipv4(ip):
    return socket.inet_ntoa(struct.pack('!I', ip))


def str_to_bytes(value):
    num = ''
    unit = ''
    for i in value:
        if i.isdigit() or i == '.':
            num = num + i
        elif i.isalnum():
            unit = unit + i
    num = float(num)
    if not unit:
        return int(num)
    if unit in ['B']:
        return int(num)
    if unit in ['k', 'K', 'kB', 'KB']:
        return int(num * 1024)
    if unit in ['m', 'M', 'mB', 'MB']:
        return int(num * 1024 * 1024)
    if unit in ['g', 'G', 'gB', 'GB']:
        return int(num * 1024 * 1024 * 1024)
    if unit in ['t', 'T', 'tB', 'TB']:
        return int(num * 1024 * 1024 * 1024 * 1024)
    print('Unit', unit, 'not understood')
    return None


def get_v4_addr_str(ip):
    # depending on the version of lttng-modules, the v4addr is a
    # string (< 2.6) or sequence (>= 2.6)
    try:
        return seq_to_ipv4(ip)
    except TypeError:
        return int_to_ipv4(ip)
