#!/usr/bin/env python3
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the 'Software'), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# KNOWN LIMITATIONS: right now this script does not behave as expected, as it
# computes the I/O on all sockets regardless of domain (i.e. unix as well as IP)
# Its behaviour also fails to account for certain sockets, which means that it
# will for instance detect wget's network activity properly, but not firefox's.
# This will be fixed once we have access to the network events' payloads.

import sys
import argparse
from babeltrace import *
from progressbar import *
from LTTngAnalyzes.common import *
from LTTngAnalyzes.sched import *
from LTTngAnalyzes.syscalls import *

class NetTop():
    def __init__(self, traces, isMeasured, number):
        self.traces = traces
        self.isMeasured = isMeasured
        self.number = number
        self.cpus = {}
        self.tids = {}
        self.syscalls = {}

    def process_event(self, event, sched, syscall):
        if event.name == 'sched_switch':
            sched.switch(event)
        elif event.name == 'sched_process_fork':
            sched.process_fork(event)
        elif event.name[0:4] == 'sys_':
            syscall.entry(event)
        elif event.name == 'exit_syscall':
            syscall.exit(event, False)

    def run(self, args):
        sched = Sched(self.cpus, self.tids)
        syscall = Syscalls(self.cpus, self.tids, self.syscalls)

        size = getFolderSize(args.path)
        widgets = ['Processing the trace: ', Percentage(), ' ',
                Bar(marker='#',left='[',right=']'), ' ', ETA(), ' ']

        if not args.no_progress:
            pbar = ProgressBar(widgets=widgets, maxval=size/BYTES_PER_EVENT)
            pbar.start()

        event_count = 0

        for event in self.traces.events:
            if not args.no_progress:
                try:
                    pbar.update(event_count)
                except ValueError:
                    pass

            self.process_event(event, sched, syscall)
            event_count += 1


        if not args.no_progress:
            pbar.finish()
            print

        self.output()

    def output(self):
        transferred = {}

        for tid in self.tids.keys():
            transferred[tid] = 0;

            for fd in self.tids[tid].fds.values():
                if fd.filename.startswith('socket'):
                    if self.isMeasured['up']:
                        transferred[tid] += fd.write
                    if self.isMeasured['down']:
                        transferred[tid] += fd.read

        for tid in sorted(transferred, key = transferred.get,
                          reverse = True)[:self.number]:
            if transferred[tid] != 0:
                print(tid, self.tids[tid].comm, convert_size(transferred[tid]))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Network usage \
    analysis by process')
    parser.add_argument('path', metavar='<path/to/trace>', help='Trace path')
    parser.add_argument('-t', '--type', type=str, default='all',
                        help='Types of network IO to measure. Possible values:\
                        all, up, down')
    parser.add_argument('-n', '--number', type=int, default=10,
                        help='Number of processes to display')
    parser.add_argument('--no-progress', action="store_true",
                        help='Don\'t display the progress bar')

    args = parser.parse_args()

    types = args.type.split(',')

    possibleTypes = ['up', 'down']

    if 'all' in types:
        isMeasured = { x: True for x in possibleTypes }
    else:
        isMeasured = { x: False for x in possibleTypes }
        for type in types:
            if type in possibleTypes:
                isMeasured[type] = True
            else:
                print('Invalid type:', type)
                parser.print_help()
                sys.exit(1)

    if args.number < 0:
        print('Number of processes must be non-negative')
        parser.print_help()
        sys.exit(1)

    traces = TraceCollection()
    handle = traces.add_trace(args.path, 'ctf')

    c = NetTop(traces, isMeasured, args.number)
    c.run(args)

    traces.remove_trace(handle)
