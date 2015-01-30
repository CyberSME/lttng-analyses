import math
import re
import time
import datetime
import socket
import struct
import sys

NSEC_PER_SEC = 1000000000
MSEC_PER_NSEC = 1000000

O_CLOEXEC = 0o2000000


class Process():
    def __init__(self):
        self.tid = -1
        self.pid = -1
        self.comm = ""
        # indexed by fd
        self.fds = {}
        # indexed by filename
        self.closed_fds = {}
        self.current_syscall = {}
        self.init_counts()

    def init_counts(self):
        self.cpu_ns = 0
        self.migrate_count = 0
        # network read/write
        self.net_read = 0
        self.net_write = 0
        # disk read/write (might be cached)
        self.disk_read = 0
        self.disk_write = 0
        # actual block access read/write
        self.block_read = 0
        self.block_write = 0
        # unclassified read/write (FD passing and statedump)
        self.unk_read = 0
        self.unk_write = 0
        # total I/O read/write
        self.read = 0
        self.write = 0
        # last TS where the process was scheduled in
        self.last_sched = 0
        # the process scheduled before this one
        self.prev_tid = -1
        # indexed by syscall_name
        self.syscalls = {}
        self.perf = {}
        self.dirty = 0
        self.allocated_pages = 0
        self.freed_pages = 0
        self.total_syscalls = 0
        # array of IORequest objects for freq analysis later (block and
        # syscalls with no FD like sys_sync)
        self.iorequests = []


class CPU():
    def __init__(self):
        self.cpu_id = -1
        self.cpu_ns = 0
        self.current_tid = -1
        self.start_task_ns = 0
        self.perf = {}
        self.wakeup_queue = []


class Syscall():
    def __init__(self):
        self.name = ""
        self.count = 0


class Disk():
    def __init__(self):
        self.name = ""
        self.prettyname = ""
        self.init_counts()

    def init_counts(self):
        self.nr_sector = 0
        self.nr_requests = 0
        self.completed_requests = 0
        self.request_time = 0
        self.pending_requests = {}
        self.rq_list = []
        self.max = None
        self.min = None
        self.total = None
        self.count = None
        self.rq_values = None
        self.stdev = None


class Iface():
    def __init__(self):
        self.name = ""
        self.init_counts()

    def init_counts(self):
        self.recv_bytes = 0
        self.recv_packets = 0
        self.send_bytes = 0
        self.send_packets = 0


class FDType():
    unknown = 0
    disk = 1
    net = 2
    # not 100% sure they are network FDs (assumed when net_dev_xmit is
    # called during a write syscall and the type in unknown).
    maybe_net = 3


class FD():
    def __init__(self):
        self.filename = ""
        self.fd = -1
        # address family
        self.family = socket.AF_UNSPEC
        self.fdtype = FDType.unknown
        # if FD was inherited, parent PID
        self.parent = -1
        self.init_counts()

    def init_counts(self):
        # network read/write
        self.net_read = 0
        self.net_write = 0
        # disk read/write (might be cached)
        self.disk_read = 0
        self.disk_write = 0
        # unclassified read/write (FD passing and statedump)
        self.unk_read = 0
        self.unk_write = 0
        # total read/write
        self.read = 0
        self.write = 0
        self.open = 0
        self.close = 0
        self.cloexec = 0
        # array of syscall IORequest objects for freq analysis later
        self.iorequests = []


class IRQ():
    HARD_IRQ = 1
    SOFT_IRQ = 2
    # from include/linux/interrupt.h
    soft_names = {0: "HI_SOFTIRQ",
                  1: "TIMER_SOFTIRQ",
                  2: "NET_TX_SOFTIRQ",
                  3: "NET_RX_SOFTIRQ",
                  4: "BLOCK_SOFTIRQ",
                  5: "BLOCK_IOPOLL_SOFTIRQ",
                  6: "TASKLET_SOFTIRQ",
                  7: "SCHED_SOFTIRQ",
                  8: "HRTIMER_SOFTIRQ",
                  9: "RCU_SOFTIRQ"}

    def __init__(self):
        self.nr = -1
        self.irqclass = 0
        self.start_ts = -1
        self.stop_ts = -1
        self.raise_ts = -1
        self.cpu_id = -1


class IORequest():
    # I/O "type"
    IO_SYSCALL = 1
    IO_BLOCK = 2
    IO_NET = 3
    # I/O operations
    OP_OPEN = 1
    OP_READ = 2
    OP_WRITE = 3
    OP_CLOSE = 4
    OP_SYNC = 5

    def __init__(self):
        # IORequest.IO_*
        self.iotype = None
        # bytes for syscalls and net, sectors for block
        # FIXME: syscalls handling vectors (vector size missing)
        self.size = None
        # for syscalls and block: delay between issue and completion
        # of the request
        self.duration = None
        # IORequest.OP_*
        self.operation = None
        # syscall name
        self.name = None
        # begin syscall timestamp
        self.begin = None
        # end syscall timestamp
        self.end = None
        # current process
        self.proc = None
        # current FD (for syscalls)
        self.fd = None
        # buffers dirtied during the operation
        self.dirty = 0
        # pages allocated during the operation
        self.page_alloc = 0
        # pages freed during the operation
        self.page_free = 0
        # pages written on disk during the operation
        self.page_written = 0
        # kswapd was forced to wakeup during the operation
        self.woke_kswapd = False
        # estimated pages flushed during a sync operation
        self.page_cleared = 0


class Syscalls_stats():
    def __init__(self):
        self.read_max = 0
        self.read_min = None
        self.read_total = 0
        self.read_count = 0
        self.read_rq = []
        self.all_read = []

        self.write_max = 0
        self.write_min = None
        self.write_total = 0
        self.write_count = 0
        self.write_rq = []
        self.all_write = []

        self.open_max = 0
        self.open_min = None
        self.open_total = 0
        self.open_count = 0
        self.open_rq = []
        self.all_open = []

        self.sync_max = 0
        self.sync_min = None
        self.sync_total = 0
        self.sync_count = 0
        self.sync_rq = []
        self.all_sync = []


class SyscallConsts():
    # TODO: decouple socket/family logic from this class
    INET_FAMILIES = [socket.AF_INET, socket.AF_INET6]
    DISK_FAMILIES = [socket.AF_UNIX]
    # list nof syscalls that open a FD on disk (in the exit_syscall event)
    DISK_OPEN_SYSCALLS = ["sys_open", "syscall_entry_open",
                          "sys_openat", "syscall_entry_openat"]
    # list of syscalls that open a FD on the network
    # (in the exit_syscall event)
    NET_OPEN_SYSCALLS = ["sys_accept", "syscall_entry_accept",
                         "sys_socket", "syscall_entry_socket"]
    # list of syscalls that can duplicate a FD
    DUP_OPEN_SYSCALLS = ["sys_fcntl", "syscall_entry_fcntl",
                         "sys_dup2", "syscall_entry_dup2"]
    SYNC_SYSCALLS = ["sys_sync", "syscall_entry_sync",
                     "sys_sync_file_range", "syscall_entry_sync_file_range",
                     "sys_fsync", "syscall_entry_fsync",
                     "sys_fdatasync", "syscall_entry_fdatasync"]
    # merge the 3 open lists
    OPEN_SYSCALLS = DISK_OPEN_SYSCALLS + NET_OPEN_SYSCALLS + DUP_OPEN_SYSCALLS
    # list of syscalls that close a FD (in the "fd =" field)
    CLOSE_SYSCALLS = ["sys_close", "syscall_entry_close"]
    # list of syscall that read on a FD, value in the exit_syscall following
    READ_SYSCALLS = ["sys_read", "syscall_entry_read",
                     "sys_recvmsg", "syscall_entry_recvmsg",
                     "sys_recvfrom", "syscall_entry_recvfrom",
                     "sys_splice", "syscall_entry_splice",
                     "sys_readv", "syscall_entry_readv",
                     "sys_sendfile64", "syscall_entry_sendfile64"]
    # list of syscall that write on a FD, value in the exit_syscall following
    WRITE_SYSCALLS = ["sys_write", "syscall_entry_write",
                      "sys_sendmsg", "syscall_entry_sendmsg",
                      "sys_sendto", "syscall_entry_sendto",
                      "sys_writev", "syscall_entry_writev"]
    # generic names assigned to special FDs, don't try to match these in the
    # closed_fds dict
    GENERIC_NAMES = ["unknown", "socket"]

    def __init__():
        pass


# imported from include/linux/kdev_t.h
def kdev_major_minor(dev):
    MINORBITS = 20
    MINORMASK = ((1 << MINORBITS) - 1)
    major = dev >> MINORBITS
    minor = dev & MINORMASK
    return "(%d,%d)" % (major, minor)


def get_disk(dev, disks):
    if dev not in disks:
        d = Disk()
        d.name = "%d" % dev
        d.prettyname = kdev_major_minor(dev)
        disks[dev] = d
    else:
        d = disks[dev]
    return d


def convert_size(size, padding_after=False, padding_before=False):
    if padding_after and size < 1024:
        space_after = " "
    else:
        space_after = ""
    if padding_before and size < 1024:
        space_before = " "
    else:
        space_before = ""
    if size <= 0:
        return "0 " + space_before + "B" + space_after
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size, 1024)))
    p = math.pow(1024, i)
    s = round(size/p, 2)
    if (s > 0):
        try:
            return '%s %s%s%s' % (s, space_before, size_name[i], space_after)
        except:
            print(i, size_name)
            raise Exception("Too big to be true")
    else:
        return '0 B'


def is_multi_day_trace_collection(handle):
    y = m = d = -1
    for h in handle.values():
        if y == -1:
            y = time.localtime(h.timestamp_begin/NSEC_PER_SEC).tm_year
            m = time.localtime(h.timestamp_begin/NSEC_PER_SEC).tm_mon
            d = time.localtime(h.timestamp_begin/NSEC_PER_SEC).tm_mday
        _y = time.localtime(h.timestamp_end/NSEC_PER_SEC).tm_year
        _m = time.localtime(h.timestamp_end/NSEC_PER_SEC).tm_mon
        _d = time.localtime(h.timestamp_end/NSEC_PER_SEC).tm_mday
        if y != _y:
            return True
        elif m != _m:
            return True
        elif d != _d:
            return True
    return False


def trace_collection_date(handle):
    if is_multi_day_trace_collection(handle):
        return None
    for h in handle.values():
        y = time.localtime(h.timestamp_begin/NSEC_PER_SEC).tm_year
        m = time.localtime(h.timestamp_begin/NSEC_PER_SEC).tm_mon
        d = time.localtime(h.timestamp_begin/NSEC_PER_SEC).tm_mday
        return (y, m, d)


def extract_timerange(handle, timerange, gmt):
    p = re.compile('^\[(?P<begin>.*),(?P<end>.*)\]$')
    if not p.match(timerange):
        return None
    b = p.search(timerange).group("begin").strip()
    e = p.search(timerange).group("end").strip()
    begin = date_to_epoch_nsec(handle, b, gmt)
    if begin is None:
        return (None, None)
    end = date_to_epoch_nsec(handle, e, gmt)
    if end is None:
        return (None, None)
    return (begin, end)


def date_to_epoch_nsec(handle, date, gmt):
    # match 2014-12-12 17:29:43.802588035 or 2014-12-12T17:29:43.802588035
    p1 = re.compile('^(?P<year>\d\d\d\d)-(?P<mon>[01]\d)-'
                    '(?P<day>[0123]\d)[\sTt]'
                    '(?P<hour>\d\d):(?P<min>\d\d):(?P<sec>\d\d).'
                    '(?P<nsec>\d\d\d\d\d\d\d\d\d)$')
    # match 2014-12-12 17:29:43 or 2014-12-12T17:29:43
    p2 = re.compile('^(?P<year>\d\d\d\d)-(?P<mon>[01]\d)-'
                    '(?P<day>[0123]\d)[\sTt]'
                    '(?P<hour>\d\d):(?P<min>\d\d):(?P<sec>\d\d)$')
    # match 17:29:43.802588035
    p3 = re.compile('^(?P<hour>\d\d):(?P<min>\d\d):(?P<sec>\d\d).'
                    '(?P<nsec>\d\d\d\d\d\d\d\d\d)$')
    # match 17:29:43
    p4 = re.compile('^(?P<hour>\d\d):(?P<min>\d\d):(?P<sec>\d\d)$')

    if p1.match(date):
        year = p1.search(date).group("year")
        month = p1.search(date).group("mon")
        day = p1.search(date).group("day")
        hour = p1.search(date).group("hour")
        minute = p1.search(date).group("min")
        sec = p1.search(date).group("sec")
        nsec = p1.search(date).group("nsec")
    elif p2.match(date):
        year = p2.search(date).group("year")
        month = p2.search(date).group("mon")
        day = p2.search(date).group("day")
        hour = p2.search(date).group("hour")
        minute = p2.search(date).group("min")
        sec = p2.search(date).group("sec")
        nsec = 0
    elif p3.match(date):
        d = trace_collection_date(handle)
        if d is None:
            print("Use the format 'yyyy-mm-dd hh:mm:ss[.nnnnnnnnn]' "
                  "for multi-day traces")
            return None
        year = d[0]
        month = d[1]
        day = d[2]
        hour = p3.search(date).group("hour")
        minute = p3.search(date).group("min")
        sec = p3.search(date).group("sec")
        nsec = p3.search(date).group("nsec")
    elif p4.match(date):
        d = trace_collection_date(handle)
        if d is None:
            print("Use the format 'yyyy-mm-dd hh:mm:ss[.nnnnnnnnn]' "
                  "for multi-day traces")
            return None
        year = d[0]
        month = d[1]
        day = d[2]
        hour = p4.search(date).group("hour")
        minute = p4.search(date).group("min")
        sec = p4.search(date).group("sec")
        nsec = 0
    else:
        return None

    d = datetime.datetime(int(year), int(month), int(day), int(hour),
                          int(minute), int(sec))
    if gmt:
        d = d + datetime.timedelta(seconds=time.timezone)
    return int(d.timestamp()) * NSEC_PER_SEC + int(nsec)


def process_date_args(args, handle):
    args.multi_day = is_multi_day_trace_collection(handle)
    if args.timerange:
        (args.begin, args.end) = extract_timerange(handle, args.timerange,
                                                   args.gmt)
        if args.begin is None or args.end is None:
            print("Invalid timeformat")
            sys.exit(1)
    else:
        if args.begin:
            args.begin = date_to_epoch_nsec(handle, args.begin, args.gmt)
            if args.begin is None:
                print("Invalid timeformat")
                sys.exit(1)
        if args.end:
            args.end = date_to_epoch_nsec(handle, args.end, args.gmt)
            if args.end is None:
                print("Invalid timeformat")
                sys.exit(1)


def ns_to_asctime(ns):
    return time.asctime(time.localtime(ns/NSEC_PER_SEC))


def ns_to_hour(ns):
    d = time.localtime(ns/NSEC_PER_SEC)
    return "%02d:%02d:%02d" % (d.tm_hour, d.tm_min, d.tm_sec)


def ns_to_hour_nsec(ns, multi_day=False, gmt=False):
    if gmt:
        d = time.gmtime(ns/NSEC_PER_SEC)
    else:
        d = time.localtime(ns/NSEC_PER_SEC)
    if multi_day:
        return "%04d-%02d-%02d %02d:%02d:%02d.%09d" % (d.tm_year, d.tm_mon,
                                                       d.tm_mday, d.tm_hour,
                                                       d.tm_min, d.tm_sec,
                                                       ns % NSEC_PER_SEC)
    else:
        return "%02d:%02d:%02d.%09d" % (d.tm_hour, d.tm_min, d.tm_sec,
                                        ns % NSEC_PER_SEC)


def ns_to_sec(ns):
    return "%lu.%09u" % (ns/NSEC_PER_SEC, ns % NSEC_PER_SEC)


def ns_to_day(ns):
    d = time.localtime(ns/NSEC_PER_SEC)
    return "%04d-%02d-%02d" % (d.tm_year, d.tm_mon, d.tm_mday)


def sec_to_hour(ns):
    d = time.localtime(ns)
    return "%02d:%02d:%02d" % (d.tm_hour, d.tm_min, d.tm_sec)


def sec_to_nsec(sec):
    return sec * NSEC_PER_SEC


def seq_to_ipv4(ip):
    return "{}.{}.{}.{}".format(ip[0], ip[1], ip[2], ip[3])


def int_to_ipv4(ip):
    return socket.inet_ntoa(struct.pack("!I", ip))


def str_to_bytes(value):
    num = ""
    unit = ""
    for i in value:
        if i.isdigit() or i == ".":
            num = num + i
        elif i.isalnum():
            unit = unit + i
    num = float(num)
    if len(unit) == 0:
        return int(num)
    if unit in ["B"]:
        return int(num)
    if unit in ["k", "K", "kB", "KB"]:
        return int(num * 1024)
    if unit in ["m", "M", "mB", "MB"]:
        return int(num * 1024 * 1024)
    if unit in ["g", "G", "gB", "GB"]:
        return int(num * 1024 * 1024 * 1024)
    if unit in ["t", "T", "tB", "TB"]:
        return int(num * 1024 * 1024 * 1024 * 1024)
    print("Unit", unit, "not understood")
    return None


def get_v4_addr_str(ip):
    # depending on the version of lttng-modules, the v4addr is a
    # string (< 2.6) or sequence (>= 2.6)
    try:
        return seq_to_ipv4(ip)
    except TypeError:
        return int_to_ipv4(ip)
