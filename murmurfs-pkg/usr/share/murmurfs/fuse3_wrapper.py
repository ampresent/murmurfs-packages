"""Minimal libfuse3 FUSE wrapper - uses ctypes Structure for struct stat."""

import ctypes
import os
import errno
import time
import struct as _struct

_libfuse = ctypes.CDLL('/usr/lib/x86_64-linux-gnu/libfuse3.so.3', use_errno=True)

class fuse_file_info(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_int),
        ("bitfields", ctypes.c_uint32),
        ("fh", ctypes.c_uint64),
        ("lock_owner", ctypes.c_uint64),
        ("poll_events", ctypes.c_uint32),
    ]

class statvfs_s(ctypes.Structure):
    _fields_ = [
        ("f_bsize", ctypes.c_ulong), ("f_frsize", ctypes.c_ulong),
        ("f_blocks", ctypes.c_ulong), ("f_bfree", ctypes.c_ulong),
        ("f_bavail", ctypes.c_ulong), ("f_files", ctypes.c_ulong),
        ("f_ffree", ctypes.c_ulong), ("f_favail", ctypes.c_ulong),
        ("f_fsid", ctypes.c_ulong), ("f_flag", ctypes.c_ulong),
        ("f_namemax", ctypes.c_ulong),
    ]

# glibc x86_64 struct stat - must match exactly what libfuse3 expects
class c_stat(ctypes.Structure):
    _fields_ = [
        ("st_dev", ctypes.c_ulong),
        ("st_ino", ctypes.c_ulong),
        ("st_nlink", ctypes.c_ulong),
        ("st_mode", ctypes.c_uint),
        ("st_uid", ctypes.c_uint),
        ("st_gid", ctypes.c_uint),
        ("__pad0", ctypes.c_int),
        ("st_rdev", ctypes.c_ulong),
        ("st_size", ctypes.c_long),
        ("st_blksize", ctypes.c_long),
        ("st_blocks", ctypes.c_long),
        ("st_atim_tv_sec", ctypes.c_long),
        ("st_atim_tv_nsec", ctypes.c_long),
        ("st_mtim_tv_sec", ctypes.c_long),
        ("st_mtim_tv_nsec", ctypes.c_long),
        ("st_ctim_tv_sec", ctypes.c_long),
        ("st_ctim_tv_nsec", ctypes.c_long),
        ("__unused0", ctypes.c_long),
        ("__unused1", ctypes.c_long),
        ("__unused2", ctypes.c_long),
    ]

assert ctypes.sizeof(c_stat) == 144, f"c_stat size is {ctypes.sizeof(c_stat)}, expected 144"

# Callback type declarations
CB_INIT = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
CB_DESTROY = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
CB_GETATTR = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(c_stat), ctypes.POINTER(fuse_file_info))
CB_OPEN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(fuse_file_info))
CB_READ = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(ctypes.c_char), ctypes.c_size_t, ctypes.c_uint64, ctypes.POINTER(fuse_file_info))
CB_WRITE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(ctypes.c_char), ctypes.c_size_t, ctypes.c_uint64, ctypes.POINTER(fuse_file_info))
CB_RELEASE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(fuse_file_info))
CB_MKDIR = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32)
CB_UNLINK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p)
CB_CREATE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32, ctypes.POINTER(fuse_file_info))
CB_TRUNCATE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(fuse_file_info))
CB_STATFS = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(statvfs_s))
CB_OPENDIR = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(fuse_file_info))
CB_READDIR = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64, ctypes.POINTER(fuse_file_info), ctypes.c_int)
CB_RELEASEDIR = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(fuse_file_info))
CB_CHMOD = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32, ctypes.POINTER(fuse_file_info))
CB_UTIMENS = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(fuse_file_info))
CB_SETXATTR = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int)
CB_GETXATTR = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t)

class fuse_operations(ctypes.Structure):
    _fields_ = [
        ("getattr", CB_GETATTR), ("readlink", ctypes.c_void_p),
        ("mknod", ctypes.c_void_p), ("mkdir", CB_MKDIR),
        ("unlink", CB_UNLINK), ("rmdir", ctypes.c_void_p),
        ("symlink", ctypes.c_void_p), ("rename", ctypes.c_void_p),
        ("link", ctypes.c_void_p), ("chmod", CB_CHMOD),
        ("chown", ctypes.c_void_p), ("truncate", CB_TRUNCATE),
        ("open", CB_OPEN), ("read", CB_READ), ("write", CB_WRITE),
        ("statfs", CB_STATFS), ("flush", ctypes.c_void_p),
        ("release", CB_RELEASE), ("fsync", ctypes.c_void_p),
        ("setxattr", CB_SETXATTR), ("getxattr", CB_GETXATTR),
        ("listxattr", ctypes.c_void_p), ("removexattr", ctypes.c_void_p),
        ("opendir", CB_OPENDIR), ("readdir", CB_READDIR),
        ("releasedir", CB_RELEASEDIR), ("fsyncdir", ctypes.c_void_p),
        ("init", CB_INIT), ("destroy", CB_DESTROY),
        ("access", ctypes.c_void_p), ("create", CB_CREATE),
        ("lock", ctypes.c_void_p), ("utimens", CB_UTIMENS),
        ("bmap", ctypes.c_void_p), ("ioctl", ctypes.c_void_p),
        ("poll", ctypes.c_void_p), ("write_buf", ctypes.c_void_p),
        ("read_buf", ctypes.c_void_p), ("flock", ctypes.c_void_p),
        ("fallocate", ctypes.c_void_p), ("copy_file_range", ctypes.c_void_p),
        ("lseek", ctypes.c_void_p),
    ]

_libfuse.fuse_main_real.restype = ctypes.c_int
_libfuse.fuse_main_real.argtypes = [
    ctypes.c_int, ctypes.POINTER(ctypes.c_char_p),
    ctypes.POINTER(fuse_operations), ctypes.c_size_t, ctypes.c_void_p,
]


def _fill_stat(st, d):
    """Fill a c_stat struct from a dict."""
    now = time.time()
    st.st_dev = d.get('st_dev', 0)
    st.st_ino = d.get('st_ino', 0)
    st.st_nlink = d.get('st_nlink', 1)
    st.st_mode = d.get('st_mode', 0o100644)
    st.st_uid = d.get('st_uid', os.getuid())
    st.st_gid = d.get('st_gid', os.getgid())
    st.__pad0 = 0
    st.st_rdev = d.get('st_rdev', 0)
    st.st_size = d.get('st_size', 0)
    st.st_blksize = d.get('st_blksize', 4096)
    st.st_blocks = d.get('st_blocks', 0)
    at = d.get('st_atime', now)
    mt = d.get('st_mtime', now)
    ct = d.get('st_ctime', now)
    st.st_atim_tv_sec = int(at)
    st.st_atim_tv_nsec = int((at - int(at)) * 1e9)
    st.st_mtim_tv_sec = int(mt)
    st.st_mtim_tv_nsec = int((mt - int(mt)) * 1e9)
    st.st_ctim_tv_sec = int(ct)
    st.st_ctim_tv_nsec = int((ct - int(ct)) * 1e9)
    st.__unused0 = 0
    st.__unused1 = 0
    st.__unused2 = 0


class FUSE3:
    def __init__(self, operations, mountpoint, **kwargs):
        self.ops = operations
        self.mountpoint = os.path.abspath(mountpoint)
        self._fuse_ops = fuse_operations()
        self._keep = []

        def mk(cbtype, fn):
            c = cbtype(fn); self._keep.append(c); return c

        o = self.ops

        def _getattr(path_raw, stat_ptr, fi):
            try:
                r = o.getattr(path_raw.decode('utf-8'), None)
                _fill_stat(stat_ptr.contents, r)
                return 0
            except OSError as e: return -e.errno
            except Exception as e:
                import sys; print(f"getattr error: {e}", file=sys.stderr)
                return -errno.EIO

        def _readdir(path_raw, buf, filler_ptr, offset, fi, flags):
            try:
                items = o.readdir(path_raw.decode('utf-8'), None)
                # filler_ptr is a fuse_fill_dir_t callback from libfuse3
                FILL_T = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p,
                                          ctypes.c_void_p, ctypes.c_uint64, ctypes.c_int)
                filler = FILL_T(filler_ptr)
                for item in items:
                    name = item if isinstance(item, str) else item[0]
                    if filler(buf, name.encode('utf-8'), None, 0, 0) != 0:
                        break
                return 0
            except OSError as e: return -e.errno
            except Exception as e:
                import sys; print(f"readdir error: {e}", file=sys.stderr)
                return -errno.EIO

        def _open(path_raw, fi):
            try:
                fh = o.open(path_raw.decode('utf-8'), fi.contents.flags if fi else 0)
                if fi: fi.contents.fh = fh
                return 0
            except OSError as e: return -e.errno
            except Exception: return -errno.EIO

        def _read(path_raw, buf, size, offset, fi):
            try:
                data = o.read(path_raw.decode('utf-8'), size, offset, fi.contents.fh if fi else 0)
                if data:
                    ctypes.memmove(buf, data, len(data))
                    return len(data)
                return 0
            except OSError as e: return -e.errno
            except Exception: return -errno.EIO

        def _write(path_raw, buf, size, offset, fi):
            try:
                data = ctypes.string_at(buf, size)
                return o.write(path_raw.decode('utf-8'), data, offset, fi.contents.fh if fi else 0)
            except OSError as e: return -e.errno
            except Exception: return -errno.EIO

        def _release(path_raw, fi):
            try:
                o.release(path_raw.decode('utf-8'), fi.contents.fh if fi else 0)
                return 0
            except OSError as e: return -e.errno
            except Exception: return -errno.EIO

        def _mkdir(path_raw, mode):
            try: o.mkdir(path_raw.decode('utf-8'), mode); return 0
            except OSError as e: return -e.errno
            except Exception: return -errno.EIO

        def _unlink(path_raw):
            try: o.unlink(path_raw.decode('utf-8')); return 0
            except OSError as e: return -e.errno
            except Exception: return -errno.EIO

        def _create(path_raw, mode, fi):
            try:
                fh = o.create(path_raw.decode('utf-8'), mode, None)
                if fi: fi.contents.fh = fh
                return 0
            except OSError as e: return -e.errno
            except Exception: return -errno.EIO

        def _truncate(path_raw, size, fi):
            try:
                o.truncate(path_raw.decode('utf-8'), size, fi.contents.fh if fi else None)
                return 0
            except OSError as e: return -e.errno
            except Exception: return -errno.EIO

        def _statfs(path_raw, stvfs):
            try:
                r = o.statfs(path_raw.decode('utf-8'))
                for k in ('f_bsize','f_frsize','f_blocks','f_bfree','f_bavail',
                          'f_files','f_ffree','f_favail','f_namemax'):
                    if k in r: setattr(stvfs.contents, k, r[k])
                return 0
            except OSError as e: return -e.errno
            except Exception: return -errno.EIO

        self._fuse_ops.getattr = mk(CB_GETATTR, _getattr)
        self._fuse_ops.readdir = mk(CB_READDIR, _readdir)
        self._fuse_ops.open = mk(CB_OPEN, _open)
        self._fuse_ops.read = mk(CB_READ, _read)
        self._fuse_ops.write = mk(CB_WRITE, _write)
        self._fuse_ops.release = mk(CB_RELEASE, _release)
        self._fuse_ops.mkdir = mk(CB_MKDIR, _mkdir)
        self._fuse_ops.unlink = mk(CB_UNLINK, _unlink)
        self._fuse_ops.create = mk(CB_CREATE, _create)
        self._fuse_ops.truncate = mk(CB_TRUNCATE, _truncate)
        self._fuse_ops.statfs = mk(CB_STATFS, _statfs)
        self._fuse_ops.setxattr = mk(CB_SETXATTR, lambda pn,n,v,sz,f: -errno.ENOTSUP)
        self._fuse_ops.getxattr = mk(CB_GETXATTR, lambda pn,n,v,sz: -errno.ENODATA)
        self._fuse_ops.chmod = mk(CB_CHMOD, lambda pn,mode,fi: 0)
        self._fuse_ops.utimens = mk(CB_UTIMENS, lambda pn,tv,fi: 0)
        self._fuse_ops.opendir = mk(CB_OPENDIR, lambda pn,fi: (setattr(fi.contents, 'fh', 0) or 0) if fi else 0)
        self._fuse_ops.releasedir = mk(CB_RELEASEDIR, lambda pn,fi: 0)

        argv = [b'murmurfs', b'-f', self.mountpoint.encode('utf-8')]
        if kwargs.get('debug'):
            argv.insert(1, b'-d')
        argc = len(argv)
        argv_c = (ctypes.c_char_p * argc)(*argv)
        self._argv_c = argv_c

        ret = _libfuse.fuse_main_real(argc, argv_c, ctypes.byref(self._fuse_ops),
                                       ctypes.sizeof(self._fuse_ops), None)
        if ret != 0:
            raise RuntimeError(f"FUSE exited with code {ret}")
