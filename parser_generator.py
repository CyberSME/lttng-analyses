#!/usr/bin/env python3
#
# Copyright 2014 Julien Desfossez <jdesfossez@efficios.com>
#
# This script takes a trace in argument and generates a Python parser ready to
# process the events (and all the fields) of the trace. It is used to generate
# all the boilerplate required to create an analysis script of a CTF trace in
# Python and allow the user to focus on the core logic of the analysis.
#
# The default resulting script can process all the events of the trace, and
# print all the fields for each event (except if you pass -q/--quiet). At the
# end of the trace, it displays also global statistics about the number of each
# event encountered.
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

import sys
import os
import stat
import argparse

try:
    from babeltrace import TraceCollection, CTFScope
except ImportError:
    # quick fix for debian-based distros
    sys.path.append("/usr/local/lib/python%d.%d/site-packages" %
                    (sys.version_info.major, sys.version_info.minor))
    from babeltrace import TraceCollection, CTFScope

preambule = """#!/usr/bin/env python3

import sys
import time
import argparse

NSEC_PER_SEC = 1000000000

try:
    from babeltrace import TraceCollection
except ImportError:
    # quick fix for debian-based distros
    sys.path.append("/usr/local/lib/python%d.%d/site-packages" %
                    (sys.version_info.major, sys.version_info.minor))
    from babeltrace import TraceCollection


class TraceParser:
    def __init__(self, trace):
        self.trace = trace
        self.event_count = {}

    def ns_to_hour_nsec(self, ns):
        d = time.localtime(ns/NSEC_PER_SEC)
        return "%02d:%02d:%02d.%09d" % (d.tm_hour, d.tm_min, d.tm_sec,
                                        ns % NSEC_PER_SEC)

    def parse(self):
        # iterate over all the events
        for event in self.trace.events:
            if not event.name in self.event_count.keys():
                self.event_count[event.name] = 0
            method_name = "handle_%s" % \
                    event.name.replace(":", "_").replace("+", "_")
            # call the function to handle each event individually
            if hasattr(TraceParser, method_name):
                func = getattr(TraceParser, method_name)
                func(self, event)
        # print statistics after parsing the trace
        print("Total event count:")
        for e in self.event_count.keys():
            print("- %s: %d" % (e, self.event_count[e]))

"""

end = """
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Trace parser')
    parser.add_argument('path', metavar="<path/to/trace>", help='Trace path')
    args = parser.parse_args()

    traces = TraceCollection()
    handle = traces.add_traces_recursive(args.path, "ctf")
    if handle is None:
        sys.exit(1)

    t = TraceParser(traces)
    t.parse()

    for h in handle.values():
        traces.remove_trace(h)
"""


def gen_parser(handle, fd, args):
    for h in handle.values():
        for event in h.events:
            fmt_str = "[%s] %s: { cpu_id = %s }, { "
            fmt_fields = "self.ns_to_hour_nsec(timestamp), event.name, " \
                         "cpu_id, "
            name = event.name.replace(":", "_").replace("+", "_")
            fd.write("    def handle_%s(self, event):\n" % (name))
            fd.write("        timestamp = event.timestamp\n")
            fd.write("        cpu_id = event[\"cpu_id\"]\n")
            for field in event.fields:
                if field.scope == CTFScope.EVENT_FIELDS:
                    fname = field.name
                    # some field names are reserved keywords/variables
                    if fname == "in":
                        fname = "_in"
                    if fname == "event":
                        fname = "_event"
                    fd.write("        %s = event[\"%s\"]\n" % (fname,
                             field.name))
                    fmt_str = fmt_str + field.name + " = %s, "
                    fmt_fields = fmt_fields + "%s, " % (fname)
            fd.write("\n        self.event_count[event.name] += 1\n")
            if not args.quiet:
                fd.write("        print(\"%s }\" %% (%s))\n\n" %
                        (fmt_str[0:-2], fmt_fields[0:-1]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Trace parser generator')
    parser.add_argument('path', metavar="<path/to/trace>", help='Trace path')
    parser.add_argument('-o', '--output', type=str, default=0,
                        metavar="<output-script-name>",
                        help='Output script name')
    parser.add_argument('-q', '--quiet', action="store_true",
                        help='Generate a quiet parser (no print)')
    args = parser.parse_args()

    traces = TraceCollection()
    handle = traces.add_traces_recursive(args.path, "ctf")
    if handle is None:
        sys.exit(1)

    if args.output == 0:
        output = "generated-parser.py"
    else:
        output = args.output

    fd = open(output, "w")
    fd.write(preambule)
    gen_parser(handle, fd, args)

    for h in handle.values():
        traces.remove_trace(h)
    fd.write(end)
    fd.close()
    os.chmod(output, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR |
             stat.S_IRGRP | stat.S_IXGRP |
             stat.S_IROTH | stat.S_IXOTH)
    print("A trace parser for this trace has been written in", output)
