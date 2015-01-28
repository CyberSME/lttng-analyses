import linuxautomaton.automaton
from lttnganalysescli import progressbar
from linuxautomaton import common
from babeltrace import TraceCollection
import argparse
import sys


class Command:
    def __init__(self, add_arguments_cb, enable_proc_filter_args=False,
                 enable_max_min_args=False):
        self._add_arguments_cb = add_arguments_cb
        self._enable_proc_filter_args = enable_proc_filter_args
        self._enable_max_min_arg = enable_max_min_args
        self._create_automaton()

    def _error(self, msg, exit_code=1):
        print(msg, file=sys.stderr)
        sys.exit(exit_code)

    def _gen_error(self, msg, exit_code=1):
        self._error('Error: {}'.format(msg), exit_code)

    def _cmdline_error(self, msg, exit_code=1):
        self._error('Command line error: {}'.format(msg), exit_code)

    def _open_trace(self):
        traces = TraceCollection()
        handle = traces.add_traces_recursive(self._arg_path, "ctf")
        if handle == {}:
            self._gen_error("Failed to open " + self._arg_path, -1)
        self._handle = handle
        self._traces = traces
        common.process_date_args(self)

    def _close_trace(self):
        for h in self._handle.values():
            self._traces.remove_trace(h)

    def _run_analysis(self, reset_cb, refresh_cb):
        self.trace_start_ts = 0
        self.trace_end_ts = 0
        self.current_sec = 0
        self.start_ns = 0
        self.end_ns = 0
        started = 0
        progressbar.progressbar_setup(self)
        if not self._arg_begin:
            started = 1
        for event in self._traces.events:
            progressbar.progressbar_update(self)
            if self._arg_begin and started == 0 and \
                    event.timestamp >= self._arg_begin:
                started = 1
                self.trace_start_ts = event.timestamp
                self.start_ns = event.timestamp
                reset_cb(event.timestamp)
            if self._arg_end and event.timestamp > self._arg_end:
                break
            if self.start_ns == 0:
                self.start_ns = event.timestamp
            if self.trace_start_ts == 0:
                self.trace_start_ts = event.timestamp
            self.end_ns = event.timestamp
            self._check_refresh(event, refresh_cb)
            self.trace_end_ts = event.timestamp
            # feed analysis
            self._analysis.process_event(event)
            # feed automaton
            self._automaton.process_event(event)
        progressbar.progressbar_finish(self)

    def _check_refresh(self, event, refresh_cb):
        """Check if we need to output something"""
        if self._arg_refresh == 0:
            return
        event_sec = event.timestamp / common.NSEC_PER_SEC
        if self.current_sec == 0:
            self.current_sec = event_sec
        elif self.current_sec != event_sec and \
                (self.current_sec + self._arg_refresh) <= event_sec:
            refresh_cb(self.start_ns, event.timestamp)
            self.current_sec = event_sec
            self.start_ns = event.timestamp

    def _validate_transform_common_args(self, args):
        self._arg_path = args.path
        if args.limit:
            self._arg_limit = args.limit
        self._arg_begin = None
        if args.begin:
            self._arg_begin = args.begin
        self._arg_end = None
        if args.end:
            self._arg_end = args.end
        self._arg_timerange = None
        if args.timerange:
            self._arg_timerange = args.timerange
        self._arg_gmt = None
        if args.gmt:
            self._arg_gmt = args.gmt
        self._arg_refresh = args.refresh
        self._arg_no_progress = args.no_progress

        if self._enable_proc_filter_args:
            self._arg_proc_list = None
            if args.procname:
                self._arg_proc_list = args.procname.split(",")
            self._arg_pid_list = None
            if args.pid:
                self._arg_pid_list = args.pid.split(",")

        if self._enable_max_min_arg:
            if args.max == -1:
                self._arg_max = None
            else:
                self._arg_max = args.max
            if args.min == -1:
                self._arg_min = None
            else:
                self._arg_min = args.min

    def _parse_args(self):
        ap = argparse.ArgumentParser(description=self._DESC)

        # common arguments
        ap.add_argument('path', metavar="<path/to/trace>", help='trace path')
        ap.add_argument('-r', '--refresh', type=int,
                        help='Refresh period in seconds', default=0)
        ap.add_argument('--limit', type=int, default=10,
                        help='Limit to top X (default = 10)')
        ap.add_argument('--no-progress', action="store_true",
                        help='Don\'t display the progress bar')
        ap.add_argument('--gmt', action="store_true",
                        help='Manipulate timestamps based on GMT instead '
                             'of local time')
        ap.add_argument('--begin', type=str, help='start time: '
                                                  'hh:mm:ss[.nnnnnnnnn]')
        ap.add_argument('--end', type=str, help='end time: '
                                                'hh:mm:ss[.nnnnnnnnn]')
        ap.add_argument('--timerange', type=str, help='time range: '
                                                      '[begin,end]')

        if self._enable_proc_filter_args:
            ap.add_argument('--procname', type=str, default=0,
                            help='Filter the results only for this list of '
                                 'process names')
            ap.add_argument('--pid', type=str, default=0,
                            help='Filter the results only for this list '
                                 'of PIDs')

        if self._enable_max_min_arg:
            ap.add_argument('--max', type=float, default=-1,
                            help='Filter out, duration longer than max usec')
            ap.add_argument('--min', type=float, default=-1,
                            help='Filter out, duration shorter than min usec')

        # specific arguments
        self._add_arguments_cb(ap)

        # version of the specific command
        ap.add_argument('-V', '--version', action='version',
                        version=self._VERSION)

        # parse arguments
        args = ap.parse_args()

        self._validate_transform_common_args(args)

        # save all arguments
        self._args = args

    def _create_automaton(self):
        self._automaton = linuxautomaton.automaton.Automaton()
