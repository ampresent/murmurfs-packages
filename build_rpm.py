#!/usr/bin/env python3
"""Build a minimal RPM package from the murmurfs-pkg directory structure."""
import struct
import os
import io
import hashlib
import time
import stat
import gzip
from pathlib import Path

# RPM constants
RPM_MAGIC = b'\xed\xab\xee\xdb'
RPM_MAJOR = 3
RPM_TYPE_BINARY = 0
RPMSIGTYPE_HEADERSIG = 5

# Header entry types
RPM_INT32 = 4
RPM_STRING = 6
RPM_BIN = 7
RPM_STRING_ARRAY = 8
RPM_I18NSTRING = 9

# Signature tags
RPMSIGTAG_SIZE = 1000
RPMSIGTAG_MD5 = 1004
RPMSIGTAG_PAYLOAD = 1007

# Main header tags
RPMTAG_NAME = 1000
RPMTAG_VERSION = 1001
RPMTAG_RELEASE = 1002
RPMTAG_SUMMARY = 1004
RPMTAG_DESCRIPTION = 1005
RPMTAG_BUILDTIME = 1006
RPMTAG_BUILDHOST = 1007
RPMTAG_SIZE = 1009
RPMTAG_DISTRIBUTION = 1010
RPMTAG_LICENSE = 1014
RPMTAG_GROUP = 1016
RPMTAG_URL = 1020
RPMTAG_OS = 1021
RPMTAG_ARCH = 1022
RPMTAG_FILESIZES = 1028
RPMTAG_FILEMODES = 1030
RPMTAG_FILERDEVS = 1033
RPMTAG_FILEMTIMES = 1034
RPMTAG_FILEMD5S = 1035
RPMTAG_FILELINKTOS = 1036
RPMTAG_FILEFLAGS = 1037
RPMTAG_FILEUSERNAME = 1039
RPMTAG_FILEGROUPNAME = 1040
RPMTAG_PROVIDENAME = 1047
RPMTAG_REQUIRENAME = 1049
RPMTAG_FILEDEVICES = 1095
RPMTAG_FILEINODES = 1096
RPMTAG_FILELANGS = 1097
RPMTAG_DIRINDEXES = 1116
RPMTAG_BASENAMES = 1117
RPMTAG_DIRNAMES = 1118
RPMTAG_PAYLOADFORMAT = 1124
RPMTAG_PAYLOADCOMPRESSOR = 1125
RPMTAG_PAYLOADFLAGS = 1126


class RPMTag:
    def __init__(self, tag, type_, count, data):
        self.tag = tag
        self.type = type_
        self.count = count
        self.data = data  # bytes


class RPMHeader:
    def __init__(self):
        self.tags = []

    def add_string(self, tag, value):
        data = value.encode('utf-8') + b'\x00'
        self.tags.append(RPMTag(tag, RPM_STRING, 1, data))

    def add_i18nstring(self, tag, value):
        data = value.encode('utf-8') + b'\x00'
        self.tags.append(RPMTag(tag, RPM_I18NSTRING, 1, data))

    def add_int32(self, tag, value):
        data = struct.pack('>I', value)
        self.tags.append(RPMTag(tag, RPM_INT32, 1, data))

    def add_int32_array(self, tag, values):
        data = struct.pack('>' + 'I' * len(values), *values)
        self.tags.append(RPMTag(tag, RPM_INT32, len(values), data))

    def add_string_array(self, tag, values):
        data = b''
        for v in values:
            data += v.encode('utf-8') + b'\x00'
        self.tags.append(RPMTag(tag, RPM_STRING_ARRAY, len(values), data))

    def add_binary(self, tag, value):
        self.tags.append(RPMTag(tag, RPM_BIN, len(value), value))

    def build(self):
        self.tags.sort(key=lambda t: t.tag)
        entries = bytearray()
        store = bytearray()
        offset = 0

        for t in self.tags:
            entries += struct.pack('>IIII', t.tag, t.type, offset, t.count)
            store += t.data
            offset += len(t.data)

        # Header: magic(4) + nindex(4) + hsize(4) + entries + store
        header = struct.pack('>III', 0x8eade801, len(self.tags), len(store))
        header = header + bytes(entries) + bytes(store)

        # Pad to 8-byte boundary
        pad = (8 - len(header) % 8) % 8
        header += b'\x00' * pad

        return header


def collect_files(pkg_dir):
    files = []
    total_size = 0
    pkg_path = Path(pkg_dir)

    for root, dirs, filenames in os.walk(pkg_path):
        for fn in filenames:
            fpath = Path(root) / fn
            rel = fpath.relative_to(pkg_path)
            rel_str = str(rel)
            # Skip DEBIAN control files
            if rel_str.startswith('DEBIAN'):
                continue
            arcname = '/' + rel_str
            size = os.path.getsize(fpath)
            total_size += size

            with open(fpath, 'rb') as f:
                content = f.read()

            mode = stat.S_IMODE(os.stat(fpath).st_mode) | stat.S_IFREG
            if fn in ('postinst', 'prerm', 'murmurfs'):
                mode = 0o755 | stat.S_IFREG

            files.append({
                'path': arcname,
                'content': content,
                'size': size,
                'mode': mode,
                'mtime': int(time.time()),
                'md5': hashlib.md5(content).hexdigest(),
            })

    return files, total_size


def build_cpio_newc(files):
    buf = bytearray()
    inode = 1

    for f in files:
        name = f['path'].lstrip('/')
        name_bytes = name.encode('utf-8') + b'\x00'

        hdr = b'070701'  # magic
        hdr += f'{inode:08x}'.encode()
        hdr += f'{f["mode"]:08x}'.encode()
        hdr += b'00000000'  # uid
        hdr += b'00000000'  # gid
        hdr += b'00000001'  # nlink
        hdr += f'{f["mtime"]:08x}'.encode()
        hdr += f'{f["size"]:08x}'.encode()
        hdr += b'00000000'  # devmajor
        hdr += b'00000000'  # devminor
        hdr += f'{len(name_bytes):08x}'.encode()
        hdr += b'00000000'  # checksum

        buf += hdr + name_bytes
        # Pad header+name to 4-byte boundary
        while len(buf) % 4:
            buf += b'\x00'

        buf += f['content']
        # Pad content to 4-byte boundary
        while len(buf) % 4:
            buf += b'\x00'

        inode += 1

    # TRAILER record
    trailer = b'070701' + b'0' * 56 + b'TRAILER!!!\x00'
    while len(trailer) % 4:
        trailer += b'\x00'
    buf += trailer

    return bytes(buf)


def build_rpm(name, version, release, summary, description, url, pkg_dir, output):
    files, total_size = collect_files(pkg_dir)
    cpio_data = build_cpio_newc(files)

    # Gzip compress payload
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode='wb', mtime=int(time.time())) as g:
        g.write(cpio_data)
    payload = gz.getvalue()

    # --- RPM Lead (96 bytes) ---
    lead = bytearray(96)
    lead[0:4] = RPM_MAGIC
    struct.pack_into('>BB', lead, 4, 3, 0)  # major, minor
    struct.pack_into('>H', lead, 6, RPM_TYPE_BINARY)
    struct.pack_into('>H', lead, 8, 0)  # archnum
    name_bytes = name.encode('utf-8')
    lead[10:76] = name_bytes.ljust(66, b'\x00')[:66]
    struct.pack_into('>H', lead, 76, 0)  # osnum
    struct.pack_into('>H', lead, 78, RPMSIGTYPE_HEADERSIG)
    nevr = f'{name}-{version}-{release}'.encode('utf-8')
    lead[80:146] = nevr.ljust(66, b'\x00')[:66]  # Actually at offset 80 but lead is 96 bytes, let's check
    # Lead is 96 bytes total: 4+2+2+2+66+2+2+2+66 = 148? No, let me recalculate
    # Actually the RPM lead format is:
    # 0-3: magic (4)
    # 4: major (1)
    # 5: minor (1)
    # 6-7: type (2)
    # 8-9: archnum (2)
    # 10-75: name (66)
    # 76-77: osnum (2)
    # 78-79: signature_type (2)
    # 80-95: reserved (16) -- BUT spec says 80-145 is also part of lead for full 96 bytes
    # Actually the lead is exactly 96 bytes. Let me just rebuild it properly.

    lead = bytearray()
    lead += RPM_MAGIC                                           # 0-3
    lead += struct.pack('>BB', 3, 0)                            # 4-5
    lead += struct.pack('>H', RPM_TYPE_BINARY)                  # 6-7
    lead += struct.pack('>H', 1)                                # 8-9 archnum (1=noarch-ish, but we set 0)
    lead += name.encode('utf-8').ljust(66, b'\x00')[:66]       # 10-75
    lead += struct.pack('>H', 1)                                # 76-77 osnum (1=linux)
    lead += struct.pack('>H', RPMSIGTYPE_HEADERSIG)             # 78-79
    lead += b'\x00' * 16                                        # 80-95 reserved
    assert len(lead) == 96, f"Lead is {len(lead)} bytes"

    # --- Signature Header ---
    sig = RPMHeader()
    sig.add_int32(RPMSIGTAG_SIZE, len(payload))
    sig.add_binary(RPMSIGTAG_MD5, hashlib.md5(payload).digest())
    sig_header = sig.build()

    # --- Main Header ---
    hdr = RPMHeader()
    hdr.add_string(RPMTAG_NAME, name)
    hdr.add_string(RPMTAG_VERSION, version)
    hdr.add_string(RPMTAG_RELEASE, release)
    hdr.add_i18nstring(RPMTAG_SUMMARY, summary)
    hdr.add_i18nstring(RPMTAG_DESCRIPTION, description)
    hdr.add_int32(RPMTAG_BUILDTIME, int(time.time()))
    hdr.add_string(RPMTAG_BUILDHOST, 'murmurfs-builder.local')
    hdr.add_int32(RPMTAG_SIZE, total_size)
    hdr.add_string(RPMTAG_DISTRIBUTION, 'MurmurFS Project')
    hdr.add_string(RPMTAG_LICENSE, 'MIT')
    hdr.add_string(RPMTAG_GROUP, 'Development/Tools')
    hdr.add_string(RPMTAG_URL, url)
    hdr.add_string(RPMTAG_OS, 'linux')
    hdr.add_string(RPMTAG_ARCH, 'noarch')
    hdr.add_string(RPMTAG_PAYLOADFORMAT, 'cpio')
    hdr.add_string(RPMTAG_PAYLOADCOMPRESSOR, 'gzip')
    hdr.add_string(RPMTAG_PAYLOADFLAGS, '6')

    # File metadata
    basenames = []
    dirnames_list = []
    dirindexes = []
    filesizes = []
    filemodes = []
    filemtimes = []
    filemd5s = []
    fileusernames = []
    filegroupnames = []
    filelangs = []
    fileddevices = []
    filerdevs = []
    fileinos = []
    fileflags = []
    dir_map = {}
    dir_idx = 0

    for f in files:
        p = f['path'].lstrip('/')
        dir_part = '/' + os.path.dirname(p) if os.path.dirname(p) else '/'
        base_part = os.path.basename(p)
        if not base_part:
            continue

        if dir_part not in dir_map:
            dir_map[dir_part] = dir_idx
            dirnames_list.append(dir_part + '/')
            dir_idx += 1

        basenames.append(base_part)
        dirindexes.append(dir_map[dir_part])
        filesizes.append(f['size'])
        filemodes.append(f['mode'])
        filemtimes.append(f['mtime'])
        filemd5s.append(f['md5'])
        fileusernames.append('root')
        filegroupnames.append('root')
        filelangs.append('')
        fileddevices.append(0)
        filerdevs.append(0)
        fileinos.append(0)
        fileflags.append(0)

    if basenames:
        hdr.add_string_array(RPMTAG_BASENAMES, basenames)
        hdr.add_string_array(RPMTAG_DIRNAMES, dirnames_list)
        hdr.add_int32_array(RPMTAG_DIRINDEXES, dirindexes)
        hdr.add_int32_array(RPMTAG_FILESIZES, filesizes)
        hdr.add_int32_array(RPMTAG_FILEMODES, filemodes)
        hdr.add_int32_array(RPMTAG_FILEMTIMES, filemtimes)
        hdr.add_string_array(RPMTAG_FILEMD5S, filemd5s)
        hdr.add_string_array(RPMTAG_FILEUSERNAME, fileusernames)
        hdr.add_string_array(RPMTAG_FILEGROUPNAME, filegroupnames)
        hdr.add_string_array(RPMTAG_FILELANGS, filelangs)
        hdr.add_int32_array(RPMTAG_FILEDEVICES, fileddevices)
        hdr.add_int32_array(RPMTAG_FILERDEVS, filerdevs)
        hdr.add_int32_array(RPMTAG_FILEINODES, fileinos)
        hdr.add_int32_array(RPMTAG_FILEFLAGS, fileflags)
        # File link tos (empty strings)
        hdr.add_string_array(RPMTAG_FILELINKTOS, [''] * len(basenames))

    hdr.add_string_array(RPMTAG_PROVIDENAME, [name])
    hdr.add_string_array(RPMTAG_REQUIRENAME, ['python3 >= 3.10'])

    main_header = hdr.build()

    # Write RPM file
    with open(output, 'wb') as out:
        out.write(lead)
        out.write(sig_header)
        out.write(main_header)
        out.write(payload)

    print(f"RPM built: {output}")
    print(f"  Size: {os.path.getsize(output)} bytes")
    print(f"  Files: {len(files)}")
    print(f"  Content size: {total_size} bytes")


if __name__ == '__main__':
    build_rpm(
        name='murmurfs',
        version='0.1.0',
        release='1',
        summary='A FUSE filesystem where AI agents store intent, not content',
        description=(
            'MurmurFS lets AI agents write intent summaries instead of full file contents.\n'
            'Files are fuzzy stacks of intention until synced, at which point an LLM\n'
            'synthesizes all layers into concrete files.\n\n'
            'This package includes a Claude/AI understanding guide at\n'
            '/usr/share/doc/murmurfs/CLAUDE_GUIDE.md that helps language models\n'
            'understand the filesystem design for optimized memory and token usage.'
        ),
        url='https://github.com/ampresent/murmurfs',
        pkg_dir='/root/.openclaw/workspace/murmurfs-pkg',
        output='/root/.openclaw/workspace/murmurfs-0.1.0-1.noarch.rpm',
    )
