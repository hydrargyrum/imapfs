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

import logging
import os
from typing import Self
import uuid

from imapfs.imapconnection import IMAPConnection


class Message:
  """Represents an IMAP message as a file-like object
  """

  def __init__(self, conn: IMAPConnection, name: str, data: bytes):
    self.conn = conn
    self.name = name
    self.data = bytearray(data)
    self.dirty = False
    self.pos = 0
    self.compress = False

  def seek(self, off, whence=os.SEEK_SET):
    """Seek in the message
    """
    if whence == os.SEEK_SET:
      self.pos = off
    elif whence == os.SEEK_CUR:
      self.pos += off
    elif whence == os.SEEK_END:
      self.pos = len(self.data) - off

  def read(self, size=None):
    """Read from the message
    """
    if size is None:
      buf = self.data[self.pos:]
      self.pos += len(buf)
      return buf
    else:
      if size + self.pos > len(self.data):
        size = len(self.data) - self.pos

      buf = self.data[self.pos:self.pos + size]
      self.pos += size
      return buf

  def truncate(self, size=None):
    """Resize the message
    """
    if size is None:
      return

    if len(self.data) > size:
      self.data = self.data[:size]
      if self.pos > size:
        self.pos = size
    else:
      self.data += b"." * (size - len(self.data))

    self.dirty = True

  def write(self, buf: bytes):
    """Write to the message
    """
    if self.pos + len(buf) > len(self.data):
      # Resize to fit
      self.truncate(self.pos + len(buf))

    self.data[self.pos:self.pos + len(buf)] = buf
    self.pos += len(buf)
    self.dirty = True

  def flush(self):
    """Write any changes to the server
    """
    if self.dirty:
      logging.debug("Flushing %d bytes", len(self.data))

      # Find old uid
      old_uid = self.conn.get_uid_by_subject(self.name)

      # Store message
      # Compress, if requested
      data_str = bytes(self.data)

      self.conn.put_message(self.name, data_str)

      # Delete old version
      if old_uid:
        self.conn.delete_message(old_uid)

      self.dirty = False

  def close(self):
    """Close the message. Writes any changes.
    """
    self.flush()

  @classmethod
  def create(cls, conn: IMAPConnection) -> Self:
    msg = cls(conn, str(uuid.uuid4()), b"")
    msg.dirty = True
    return msg

  @classmethod
  def open(cls, conn: IMAPConnection, name: str) -> Self:
    """Open a message with name `name'
    Raises IOError if not found
    """
    # Find message with subject 'name'
    uid = conn.get_uid_by_subject(name)
    if not uid:
      raise IOError()

    data = conn.get_message(uid)
    if data is None:
      raise IOError()

    msg = cls(conn, name, data)

    return msg

  @staticmethod
  def unlink(conn, name: str) -> None:
    """Delete a message with name `name'
    """

    uid = conn.get_uid_by_subject(name)
    if not uid:
      return

    conn.delete_message(uid)
