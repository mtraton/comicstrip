#!/usr/bin/env python
#
# Comicstrip - extract individual frames from a comic book/strip
#
# Copyright 2009 David Koo
#
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU Affero General Public License as published by the Free Software Foundation, either
# version 3 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License along with this
# program.  If not, see <http://www.gnu.org/licenses/>.
#


import sys
import inspect
import zipfile
import os

import fnmatch

from PIL import Image
from math import log
from PIL import ImageFile
from PIL import ImageEnhance
#from ImageFile import Parser
#from ImageEnhance import Contrast
#from ImageFilter import BLUR
from PIL import ImageFilter
from optparse import OptionParser
#from ImageOps import autocontrast
from PIL import ImageOps
_version = "0.1"

# gutter color
gcolor = 255

# gutter width
gwidth, gheight = 10, 10

# we first change the contrast of the image (to remove noise in the gutters) and then digitize
# them. This tells us how much to set the contrast to
contrast = 0.8

# barrier (in terms of a scale from 1 to 255). Used in dividing the image into black and
# white portions - if a color is < barrier then it is converted to black else white. The
# numbers here come from trial and error!
barrier = 210


def debug(switch, *args):
    if switch:
        callerframe = inspect.getouterframes(inspect.currentframe())[1]
        line, caller = callerframe[2], callerframe[3]
        context = "%s:%d" % (caller, line)
        print "%-20s:" % (context), " ".join(map(str, args))


def nopfn(*args):
    pass


class page(object):
    """A page of the book"""

    # Typical Layout of a page:
    #   +-Page-----------------------------------------+
    #   |                                              |<- Gutter
    #   | +----------+ +--------------+ +-----------+  |-----
    #   | |          | |              | |           |  | Row
    #   | |          | |              | |           |  |
    #   | +----------+ +--------------+ +-----------+  |-----
    #   |                                              |<- Gutter
    #   | +----------+ +--------------+ +-----------+  |-----
    #   | |          | |              | |           |  | Row
    #   | |          | |              | |           |  |
    #   | +----------+ +--------------+ +-----------+  |-----
    #   |                                              |<- Gutter
    #   +----------------------------------------------+
    #                <->
    #              Gutter
    #
    # The left right, top and bottom gutters of the page may not be present
    #
    # Algo:
    #   - Starting with a 'startRow' we keep moving down as long as the whole row is still a
    #     gutter. This gives us a row start
    #   - Now move down "fheight" pixels (where fheight is the minimum height of a frame)
    #     and keep moving down until a gutter is encountered (or bottom of page). That is the
    #     bottom of our row
    #
    # Once we have our rows we repeat a similar procedure for each row to get the frames
    # of each row. We could transpose the whole image, copy out each row and use the row
    # detection functions for extracting the columns, but we want to use as little copies as
    # possible.
    #
    # To make the detection of boundaries easier, we first increase the contrast and remove any
    # gradients. This, unfortunately involves making 2 copies of the image (PIL, unfortunately,
    # (for most part) does not support in-place operations) :(.

    def _isGutterRow(self, left, row, right):
        """Is the row from [left, right) a gutter?"""
        nongutter = [x for x in xrange(left, right) if gcolor != self.img.getpixel((x,row))]
        return len(nongutter) == 0

    def _isGutterCol(self, col, top, bot):
        """Is the column from [top, bot) a gutter?"""
        nongutter = [r for r in xrange(top, bot) if gcolor != self.img.getpixel((col,r))]
        return len(nongutter) == 0

    def _getRow(self, l, startRow, r, b):
        """Get the first row of (l, startRow, r, b).
        We don't use page members directly 'coz this function is called to further refine each
        frame's top and bottom boundaries"""
        debug(self.debug, "startRow:", startRow)
        if startRow >= b:
            return (-1,-1)

        # move down as long as the entire row (within window) is "gutter color"
        row1 = startRow
        while row1 < b and self._isGutterRow(l, row1, r):
            row1 += 1
        debug(self.debug, "row1:", row1)
        if row1 == b:
            return (-1, -1) # We've finished the image, no more rows

        # Now to find the bottom gutter - we assume a frame at least fheight pixels
        # high so we'll just skip those many pixels
        row2 = row1 + self.fheight
        debug(self.debug, "row2 starting with:", row2)
        if row2 > b:
            return (-1, -1) # probably looking at the area after the last row (e.g. pagenum)
        while row2 < b and not self._isGutterRow(l, row2, r):
            row2 += 1

        debug(self.debug, "row2:", row2)
        if row2 - row1 < self.fheight:
            return (-1, -1) # not a proper frame (e.g. contains pagenum)
        return (row1, row2)

    def _prnfn(self, symbol):
        print symbol,
        sys.stdout.flush()

    def _nlfn(self):
        print

    def _getRows(self, startRow):
        """Get a list of all rows starting from startRow. Display progress if indicated"""
        top, rows = startRow, []
        count = 0
        l,r,b = self.lignore, self.img.size[0] - self.rignore, self.img.size[1] - 1
        while True:
            top, bot = self._getRow(l, top, r, b)
            if top != -1:
                debug(self.debug, "got row:", top, bot)
                rows.append((0, top, self.img.size[0]-1, bot))
                top = bot + (gheight//2)
                count += 1
            else:
                debug(self.debug, "No more rows")
                break
        debug(self.debug, "rows:", rows)
        return rows

    def _getCol(self, startCol, t, b):
        """Get leftmost column of a row starting from startCol.
        The row is enclosed by t and b."""
        debug(self.debug, "startCol, t, b:", startCol, t, b)
        r = self.img.size[0] - 1
        if startCol >= r:
            return (-1,-1)

        # move right as long as the entire column (within window) is "gutter color"
        col1 = startCol
        while col1 < r and self._isGutterCol(col1, t, b):
            col1 += 1
        if col1 == r:
            return (-1, -1) # We've finished the row, no more columns
        debug(self.debug, "col1:", col1)

        # Now to find the right boundary of the frame
        col2 = col1 + self.fwidth
        debug(self.debug, "Starting with column:", col2)
        if col2 > r:
            return (-1, -1) # no frame here - just gutter area on the right
        while col2 < r and not self._isGutterCol(col2, t, b):
            col2 += 1
        debug(self.debug, "col2:", col2)

        if col2 - col1 < self.fwidth:
            return (-1, -1) # not a proper frame
        return (col1, col2)

    def _getCols(self, rt, rb):
        """Get columns from row bounded on top and bottom by rt & rb."""
        left, cols = 0, []
        while True:
            left, right = self._getCol(left, rt, rb)
            if left != -1:
                debug(self.debug, "got column:", left, right)
                cols.append((left, rt, right, rb))
                left = right + (gwidth//2)
            else:
                debug(self.debug, "No more columns")
                break
        debug(self.debug, "cols:", cols)
        return cols

    def _getFrames(self):
        """Get all frames in page"""
        # Display progress in the form of ....Pg....Pg.... (1 dot per page)
        if self.pgNum % 5 == 0:
            symbol = str(self.pgNum)
        else:
            symbol = "."
        self.prnfn(symbol)
        # first get all the rows, traversing the entire height of the image (after
        # accounting for the adjustments
        rows = self._getRows(self.startRow)
        debug(self.debug, "Got rows:", rows)

        # now get columns in each row
        frames = []
        for rl, rt, rr, rb in rows:
            debug(self.debug, "Row:", rl, rt, rr, rb)
            cols = self._getCols(rt, rb)
            debug(self.debug, "Got Columns:", cols)
            frames.extend(cols)

        debug(self.debug, "=== Frames:", frames)
        # Now try to further trim the top and bottom gutters of each frame (left and right
        # gutters would already be as tight as possible) and then extract the area from the
        # original image
        fimgs = []
        for (fl, ft, fr, fb) in frames:
            debug(self.debug, "Refining:", fl, ft, fr, fb)
            newt, newb = self._getRow(fl, ft, fr, fb)
            if newt == -1:
                # The frame is already as tight as possible
                debug(self.debug, "Cannot reduce any further")
                newt, newb = ft, fb
            else:
                debug(self.debug, "Got:", newt, newb)
            fimg = Image.new("RGB", (fr - fl, newb - newt))
            fimg = Image.new("RGB", (fr - fl, newb - newt))
            fimg.paste(self.orig.crop((fl, newt, fr, newb)), (0, 0))
            fimgs.append(fimg)
        return fimgs

    def _digitize(self, color):
        if color // barrier == 0:
            result = 0
        else:
            result = 255
        return result

    def _prepare(self):
        bwimg = self.orig.convert("L")
        return ImageEnhance.Contrast(ImageOps.autocontrast(bwimg, 10)).enhance(contrast).point(self._digitize)

    keys = ["startRow", "lignore", "rignore", "contents", "infile", "pgNum", "quiet",
            "debug", "fwidth", "fheight"]

    def __init__(self, **kw):
        """A page object.
        Valid keyword parameters:
        startRow (default:0):
            Which row to start analyzing from. This is typically used for analyzing the first
            page of a comic where some top part of the page contains the title (which we need
            to skip)
        lignore, rignore (default:0 for both):
            Scanned pages might have some non-white color on one or both of the edges which
            interfere with gutter detection. These paramters tell the gutter detection
            algorithm to adjust the left boundary by lignore and right boundary by
            rignore when locating gutters
        contents (default: True):
            True => "infile" is a string consisting of page contents
            False => "infile" is a string holding the name of the page file to open
        infile:
            A String holding page contents or the page file name (depending on the contents
            parameter)
        pgNum (default:1):
            Page number (used when processing a whole book)
        quiet:
            Don't print any status messages
        debug:
            Enable debug prints
        fwidth, fheight:
            Minimum width (height resp) of a frame"""
        object.__init__(self)
        [self.__setattr__(k, kw[k]) for k in page.keys]
        quietFns = {False:(self._prnfn, self._nlfn), True:(nopfn, nopfn)}
        self.prnfn, self.nlfn = quietFns[self.quiet]
        if self.contents:
            parser = Parser()
            parser.feed(kw["infile"])
            self.orig = parser.close()
        else:
            self.orig = Image.open(self.infile)
        self.img = self._prepare()
        self.frames = self._getFrames()

    def save(self, prefix="", counter=0):
        debug(self.debug, "Saving pages:")
        npfxDigits = int(log(len(self.frames), 10)) + 1
        for fimg in self.frames:
            fname = "%s%0*d.jpg" % (prefix, npfxDigits, counter)
            debug(self.debug, "    Saving:", fname)
            fimg.save(fname)
            counter += 1
        return counter


class comic(object):
    """A comic book - a cbz or a cbr file"""

    PROCESS_BOOK = 0
    PROCESS_PAGE = 1

    keys = ["infile", "prefix", "firstPg", "firstPgRow", "startRow", "lignore", "rignore",
            "filePat", "fileList", "quiet", "gwidth", "debug", "fwidth", "fheight"]
    pgkeys = ["startRow", "lignore", "rignore", "infile", "quiet", "debug", "fwidth", "fheight"]

    def __init__(self, **kw):
        """Initialize a comic book (cbz) file.
        Permitted arguments
        infile:
            Name of the comic book to process. Only cbz files are supported at the moment.
        prefix:
            Output file prefix. Each output fill will be of the form prefixNNN
        firstPg:
            The name of the first page to start analyzing from.
        firstPgRow:
            The first page might start with a title first. We need to skip over this to
            get to the actual rows
        lignore, rignore:
            Sometimes scanned pages are not at the edges. lignore, rignore tell us
            how many left/right columns to ignore when searching for rows
        filePat:
            File names in the archive that match this pattern will be processed. The pattern is
            a glob expression. If both fileList and filePat are specified, fileList is used, if
            neither are specified then the file pattern "*.jpg" is used.
        fileList:
            File names in the archive that are in this list will be processed. If both fileList
            and filePat are specified, fileList is used, if neither are specified then the file
            pattern "*.jpg" is used.
        gwidth:
            Minimum width (and height) of the gutter.
        fwidth, fheight:
            Minimum width (height resp) of a frame.
        quiet:
            Don't print any progress messages.
        debug:
            Enable debug prints"""
        object.__init__(self)
        [self.__setattr__(k, kw[k]) for k in comic.keys]
        self.counter = 0 # used when writing output
        try:
            self.zfile = zipfile.ZipFile(kw["infile"])
        except:
            # is probably a single page image instead of a comic book
            self.actionType = comic.PROCESS_PAGE
        else:
            self.actionType = comic.PROCESS_BOOK

    def processBook(self):
        if len(self.fileList) == 0:
            self.fileList = fnmatch.filter(self.zfile.namelist(), self.filePat)
            self.fileList.remove(self.firstPg)
        kw = dict([(k, object.__getattribute__(self, k)) for k in comic.pgkeys])
        kw["pgNum"] = 1
        kw["contents"] = True
        if self.firstPg:
            # process the start page separately
            buf = self.zfile.read(self.firstPg)
            kw["startRow"] = self.firstPgRow
            kw["infile"] = buf
            pg = page(**kw)
            self.counter = pg.save(self.prefix, self.counter)
            kw["pgNum"] += 1

        # for other pages, startRow = startRow
        kw["startRow"] = self.startRow
        for fname in self.fileList:
            buf = self.zfile.read(fname)
            kw["infile"] = buf
            pg = page(**kw)
            self.counter = pg.save(self.prefix, self.counter)
            kw["pgNum"] += 1

    def processPg(self):
        kw = dict([(k, object.__getattribute__(self, k)) for k in comic.pgkeys])
        kw["pgNum"] = 1
        kw["contents"] = False
        page(**kw).save(self.prefix, self.counter)

    def process(self):
        if self.actionType == comic.PROCESS_BOOK:
            self.processBook()
        else:
            self.processPg()
        print

def pgtest():
    global DEBUG
    # DEBUG = True
    if len(sys.argv) == 3:
        startrow = int(sys.argv[1])
    else:
        startrow = 0
    pgs = sys.argv[2:]
    counter = 11
    for pgname in pgs:
        print "Processing page:", pgname
        pg = page(pgname, startrow, 50, 50)
        counter = pg.save("ottokar-", counter)


def getargs(parser):
    (options, args) = parser.parse_args()
    kw = {}
    kw["infile"] = options.infile
    if kw["infile"] is None:
        raise ValueError, "Input File Not Specified"
    kw["prefix"] = options.prefix
    kw["firstPg"] = options.firstPg
    kw["firstPgRow"] = options.firstPgRow
    kw["startRow"] = options.startRow
    kw["lignore"] = options.lignore
    kw["rignore"] = options.rignore
    kw["filePat"] = options.filePat
    kw["quiet"] = options.quiet
    kw["gwidth"] = options.gwidth
    kw["fwidth"] = options.fwidth
    kw["fheight"] = options.fheight
    kw["debug"] = options.debug
    kw["fileList"] = args
    return kw


parser = OptionParser(usage="%prog [options] [pgfile1, pgfile2, ...]",
                      version="%%prog %s" % (_version),
                      description="Split a comic page into individual frames")
parser.add_option("-q", "--quiet", action="store_true", dest="quiet",
                  help="Don't print progress messages to stdout [default:%default]")
parser.add_option("-d", "--debug", dest="debug", action="store_true",
                  help="Enable debug prints [default:%default]")
parser.add_option("-f", "--file", dest="infile", type="string", metavar="FILE",
                  help="Name of the input file")

parser.add_option("", "--prefix", dest="prefix",
                  help="Prefix for output files")
parser.add_option("", "--left-ignore", type="int", dest="lignore", metavar="PIXELS",
                  help="How much of the left margin to ignore when detecting rows [default:%default]")
parser.add_option("", "--right-ignore", type="int", dest="rignore", metavar="PIXELS",
                  help="How much of the right margin to ignore when detecting rows [default:%default]")
parser.add_option("", "--firstpage", dest="firstPg", type="string", metavar="PGFILENAME",
                  help="Name of the title page in comic archive file")
parser.add_option("", "--firstpg-row", type="int", dest="firstPgRow", metavar="PIXELS",
                  help="From which line of the first page should the processing start [default:%default]")
parser.add_option("", "--startrow", type="int", dest="startRow", metavar="PIXELS",
                  help="From which line of the each page (other than the first page) should the processing start [default:%default]")
parser.add_option("", "--glob", dest="filePat", metavar="GLOB",
                  help="A glob expression to select files to be processed from the book. (Not required if a file list is provided.)")
parser.add_option("", "--gutter-width", dest="gwidth", metavar="WIDTH",
                  help="Minimum width of the gutter [default:%default]")
parser.add_option("", "--min-width", dest="fwidth", metavar="WIDTH", type="int",
                  help="Minimum width of a frame [default:%default]")
parser.add_option("", "--min-height", dest="fheight", metavar="HEIGHT", type="int",
                  help="Minimum height of a frame [default:%default]")

parser.set_defaults(quiet=False,
                    prefix="cstrip-",
                    lignore=0,
                    rignore=0,
                    firstPgRow=0,
                    startRow=0,
                    gwidth=15,
                    fwidth=100,
                    fheight=100,
                    debug=False)

kw = getargs(parser)
book = comic(**kw)
book.process()

# vim:tw=95
