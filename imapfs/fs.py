# IMAPFS - Cloud storage via IMAP
# Copyright (C) 2013 Wes Weber
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from imapfs.debug_print import debug_print
from imapfs import directory, file, message
import fuse
import stat
import uuid

ROOT = str(uuid.UUID(bytes='\0' * 16))

fuse.fuse_python_api = (0, 2)

class IMAPFS(fuse.Fuse):
  """FUSE object for imapfs
  """

  def __init__(self, imap, *args, **kwargs):
    self.imap = imap
    fuse.Fuse.__init__(self, *args, **kwargs)
    self.open_nodes = {}

  def open_node(self, name):
    """Opens a node (file or directory)
    """

    # Check cache
    if name in self.open_nodes:
      return self.open_nodes[name]

    try:
      msg = message.Message.open(self.imap, name)
      if not msg:
        return None
    except:
      return None

    # Determine file or dir
    type_code = chr(msg.data[0])

    if type_code == 'f':
      obj = file.File.from_message(msg)
      debug_print("Opening file %s" % name)
    elif type_code == 'd':
      obj = directory.Directory.from_message(msg)
      debug_print("Opening directory %s" % name)

    self.open_nodes[name] = obj
    return obj

  def close_node(self, node):
    """Close an open node
    """
    debug_print("Closing node %s" % node.message.name)
    node.close()
    if node.message.name in self.open_nodes:
      self.open_nodes.pop(node.message.name)

  def check_filesystem(self):
    """Check if there is a filesystem present
    Returns True, False or None
    Returns True when a filesystem is successfully located
    Returns False when a filesystem is present, but cannot be decrypted
    Returns None when no filesystem is found
    """
    root = self.open_node(ROOT)
    if not root or root.__class__ != directory.Directory:
      return False

    # check if decrypted properly
    data = str(root.message.data[0:3])
    if data != "d\r\n":
      return False

    return True

  def init_filesystem(self):
    """Create a filesystem
    """
    root = directory.Directory.create(self.imap)
    root.message.name = ROOT
    root.close()

  def get_node_by_path(self, path):
    """Open the node specified by path
    Walks through the directory tree to find the node
    """
    # handle root
    if path == "/":
      return self.open_node(ROOT)

    # split into directory parts
    parts = path.split("/")
    current_node = self.open_node(ROOT)
    for part in parts:
      if not part:  # blank entry from double slashes
        continue

      # Trying to get the child of a file?
      if current_node.__class__ != directory.Directory:
        break

      # find children
      child_key = current_node.get_child_by_name(part)
      if not child_key:
        return None

      # Open child, then set it to be searched
      child_node = self.open_node(child_key)
      if not child_node:
        return None
      current_node = child_node
    return current_node

  def get_path_parent(self, path):
    """Gets the parent part of a path
    """
    parts = path.rpartition("/")
    return parts[0]

  def get_path_filename(self, path):
    """Gets the filename part of a path
    """
    parts = path.rpartition("/")
    return parts[2]

  #
  # Filesystem functions
  #

  def getattr(self, path):
    node = self.get_node_by_path(path)

    if not node:
      return -fuse.ENOENT

    st = fuse.Stat()

    if node.__class__ == directory.Directory:
      st.st_mode = stat.S_IFDIR | 0777
      st.st_nlink = 2
      st.st_size = 4096
    else:
      st.st_mode = stat.S_IFREG | 0666
      st.st_nlink = 1
      st.st_size = node.size

    return st

  def readdir(self, path, offset):
    node = self.get_node_by_path(path)
    if node.__class__ != directory.Directory:
      return

    yield fuse.Direntry(".")
    yield fuse.Direntry("..")

    for child_key, child_name in node.children.items():
      yield fuse.Direntry(child_name)

  def mkdir(self, path, mode):
    node = self.get_node_by_path(path)
    if node:
      return -fuse.EEXIST

    parent = self.get_node_by_path(self.get_path_parent(path))
    if not parent:
      return -fuse.EEXIST

    child = directory.Directory.create(self.imap)
    self.open_nodes[child.message.name] = child
    parent.add_child(child.message.name, self.get_path_filename(path))

    child.flush()
    parent.flush()

  def rmdir(self, path):
    child = self.get_node_by_path(path)
    if not child:
      return -fuse.ENOENT

    if len(child.children) > 0:
      return -fuse.ENOTEMPTY

    parent = self.get_node_by_path(self.get_path_parent(path))
    if not parent:
      return -fuse.ENOENT

    parent.remove_child(child.message.name)
    parent.flush()
    self.close_node(child)
    message.Message.unlink(self.imap, child.message.name)

  def mknod(self, path, mode, dev):
    node = self.get_node_by_path(path)
    if node:
      return -fuse.EEXIST

    parent = self.get_node_by_path(self.get_path_parent(path))
    if not parent:
      return -fuse.ENOENT

    node = file.File.create(self.imap)
    self.open_nodes[node.message.name] = node
    parent.add_child(node.message.name, self.get_path_filename(path))

    self.close_node(node)
    parent.flush()

  def utime(self, path, times):
    node = self.get_node_by_path(path)
    if not node:
      return -fuse.ENOENT

    node.mtime = times[1]
    node.dirty = True
    node.flush()

  def unlink(self, path):
    node = self.get_node_by_path(path)
    if not node or node.__class__ != file.File:
      return -fuse.ENOENT

    parent = self.get_node_by_path(self.get_path_parent(path))
    if not parent:
      return -fuse.ENOENT

    parent.remove_child(node.message.name)
    parent.flush()
    node.delete()
    self.open_nodes.pop(node.message.name)

  def truncate(self, path, size):
    node = self.get_node_by_path(path)
    if not node:
      return -fuse.ENOENT

    if node.__class__ != file.File:
      return -fuse.EISDIR

    node.truncate(size)
    node.flush()

  def read(self, path, size, offset):
    node = self.get_node_by_path(path)
    if not node:
      return -fuse.ENOENT
    if node.__class__ != file.File:
      return -fuse.EISDIR

    node.seek(offset)
    data = str(node.read(size))

    debug_print("Read %d-%d returned %d bytes" % (offset, size + offset, len(data)))

    return data

  def write(self, path, buf, offset):
    node = self.get_node_by_path(path)
    if not node:
      return -fuse.ENOENT
    if node.__class__ != file.File:
      return -fuse.EISDIR

    byte_buf = bytearray(buf)

    node.seek(offset)
    node.write(byte_buf)

    debug_print("Write %d-%d" % (offset, offset + len(buf)))

    return len(buf)

  def release(self, path, flags):
    node = self.get_node_by_path(path)
    if not node:
      return -fuse.ENOENT

    self.close_node(node)