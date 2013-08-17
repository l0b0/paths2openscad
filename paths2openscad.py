#!/usr/bin/env python

# openscad.py

# This is an Inkscape extension to output paths to extruded OpenSCAD polygons
# The Inkscape objects must first be converted to paths (Path > Object to Path).
# Some paths may not work well -- the paths have to be polygons.  As such,
# paths derived from text may meet with mixed results.

# Written by Daniel C. Newman ( dan dot newman at mtbaldy dot us )
# 10 June 2012

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

import math
import os.path
import inkex
import simplepath
import simplestyle
import simpletransform
import cubicsuperpath
import cspsubdiv
import bezmisc

DEFAULT_WIDTH = 100
DEFAULT_HEIGHT = 100

def parseLengthWithUnits( str ):

	'''
	Parse an SVG value which may or may not have units attached
	This version is greatly simplified in that it only allows: no units,
	units of px, and units of %.  Everything else, it returns None for.
	There is a more general routine to consider in scour.py if more
	generality is ever needed.
	'''

	u = 'px'
	s = str.strip()
	if s[-2:] == 'px':
		s = s[:-2]
	elif s[-1:] == '%':
		u = '%'
		s = s[:-1]

	try:
		v = float( s )
	except:
		return None, None

	return v, u

def subdivideCubicPath( sp, flat, i=1 ):

	'''
	[ Lifted from eggbot.py with impunity ]

	Break up a bezier curve into smaller curves, each of which
	is approximately a straight line within a given tolerance
	(the "smoothness" defined by [flat]).

	This is a modified version of cspsubdiv.cspsubdiv(): rewritten
	because recursion-depth errors on complicated line segments
	could occur with cspsubdiv.cspsubdiv().
	'''

	while True:
		while True:
			if i >= len( sp ):
				return

			p0 = sp[i - 1][1]
			p1 = sp[i - 1][2]
			p2 = sp[i][0]
			p3 = sp[i][1]

			b = ( p0, p1, p2, p3 )

			if cspsubdiv.maxdist( b ) > flat:
				break

			i += 1

		one, two = bezmisc.beziersplitatt( b, 0.5 )
		sp[i - 1][2] = one[1]
		sp[i][0] = two[2]
		p = [one[2], one[3], two[1]]
		sp[i:1] = [p]

class OpenSCAD( inkex.Effect ):

	def __init__( self ):

		inkex.Effect.__init__( self )

		self.OptionParser.add_option( "--tab",	#NOTE: value is not used.
			action="store", type="string",
			dest="tab", default="splash",
			help="The active tab when Apply was pressed" )

		self.OptionParser.add_option('--smoothness', dest='smoothness',
			type='float', default=float( 0.2 ), action='store',
			help='Curve smoothing (less for more)' )

		self.OptionParser.add_option('--height', dest='height',
			type='string', default='5', action='store',
			help='Height (mm)' )

		self.OptionParser.add_option('--fname', dest='fname',
			type='string', default='~/inkscape.scad',
			action='store',
			help='Curve smoothing (less for more)' )

		self.cx = float( DEFAULT_WIDTH ) / 2.0
		self.cy = float( DEFAULT_HEIGHT ) / 2.0
		self.xmin, self.xmax = ( 1.0E70, -1.0E70 )
		self.ymin, self.ymax = ( 1.0E70, -1.0E70 )

		# Dictionary of paths we will construct.  It's keyed by the SVG node
		# it came from.  Such keying isn't too useful in this specific case,
		# but it can be useful in other applications when you actually want
		# to go back and update the SVG document
		self.paths = {}

		# Output file handling
		self.call_list = []
		self.pathid    = int( 0 )
		self.f = None

		# For handling an SVG viewbox attribute, we will need to know the
		# values of the document's <svg> width and height attributes as well
		# as establishing a transform from the viewbox to the display.

		self.docWidth  = float( DEFAULT_WIDTH )
		self.docHeight = float( DEFAULT_HEIGHT )
		self.docTransform = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]

	def getLength( self, name, default ):

		'''
		Get the <svg> attribute with name "name" and default value "default"
		Parse the attribute into a value and associated units.  Then, accept
		units of cm, ft, in, m, mm, pc, or pt.  Convert to pixels.

		Note that SVG defines 90 px = 1 in = 25.4 mm.
		'''

		str = self.document.getroot().get( name )
		if str:
			v, u = parseLengthWithUnits( str )
			if not v:
				# Couldn't parse the value
				return None
			elif ( u == 'mm' ):
				return float( v ) * ( 90.0 / 25.4 )
			elif ( u == 'cm' ):
				return float( v ) * ( 90.0 * 10.0 / 25.4 )
			elif ( u == 'm' ):
				return float( v ) * ( 90.0 * 1000.0 / 25.4 )
			elif ( u == 'in' ):
				return float( v ) * 90.0
			elif ( u == 'ft' ):
				return float( v ) * 12.0 * 90.0
			elif ( u == 'pt' ):
				# Use modern "Postscript" points of 72 pt = 1 in instead
				# of the traditional 72.27 pt = 1 in
				return float( v ) * ( 90.0 / 72.0 )
			elif ( u == 'pc' ):
				return float( v ) * ( 90.0 / 6.0 )
			elif ( u == 'px' ):
				return float( v )
			else:
				# Unsupported units
				return None
		else:
			# No width specified; assume the default value
			return float( default )

	def getDocProps( self ):

		'''
		Get the document's height and width attributes from the <svg> tag.
		Use a default value in case the property is not present or is
		expressed in units of percentages.
		'''

		self.docHeight = self.getLength( 'height', DEFAULT_HEIGHT )
		self.docWidth = self.getLength( 'width', DEFAULT_WIDTH )
		if ( self.docHeight == None ) or ( self.docWidth == None ):
			return False
		else:
			return True

	def handleViewBox( self ):

		'''
		Set up the document-wide transform in the event that the document has an SVG viewbox
		'''

		if self.getDocProps():
			viewbox = self.document.getroot().get( 'viewBox' )
			if viewbox:
				vinfo = viewbox.strip().replace( ',', ' ' ).split( ' ' )
				if ( vinfo[2] != 0 ) and ( vinfo[3] != 0 ):
					sx = self.docWidth / float( vinfo[2] )
					sy = self.docHeight / float( vinfo[3] )
					self.docTransform = simpletransform.parseTransform( 'scale(%f,%f)' % (sx, sy) )

	def getPathVertices( self, path, node=None, transform=None, find_bbox=True ):

		'''
		Decompose the path data from an SVG element into individual
		subpaths, each subpath consisting of absolute move to and line
		to coordinates.  Place these coordinates into a list of polygon
		vertices.
		'''

		if ( not path ) or ( len( path ) == 0 ):
			# Nothing to do
			return None

		# parsePath() may raise an exception.  This is okay
		sp = simplepath.parsePath( path )
		if ( not sp ) or ( len( sp ) == 0 ):
			# Path must have been devoid of any real content
			return None

		# Get a cubic super path
		p = cubicsuperpath.CubicSuperPath( sp )
		if ( not p ) or ( len( p ) == 0 ):
			# Probably never happens, but...
			return None

		if transform:
			simpletransform.applyTransformToPath( transform, p )

		# Now traverse the cubic super path
		subpath_list = []
		subpath_vertices = []
		for sp in p:
			# We've started a new subpath
			# See if there is a prior subpath and whether we should keep it
			if len( subpath_vertices ):
				subpath_list.append( subpath_vertices )
			subpath_vertices = []
			subdivideCubicPath( sp, float( self.options.smoothness ) )
			for csp in sp:
				pt = csp[1]
				subpath_vertices.append( pt )
				if find_bbox:
					if pt[0] < self.xmin:
						self.xmin = pt[0]
					elif pt[0] > self.xmax:
						self.xmax = pt[0]
					if pt[1] < self.ymin:
						self.ymin = pt[1]
					elif pt[1] > self.ymax:
						self.ymax = pt[1]

		# Handle final subpath
		if len( subpath_vertices ):
			subpath_list.append( subpath_vertices )

		if len( subpath_list ) > 0:
			self.paths[node] = subpath_list

	def convertPath( self, node ):

		# Generate an OpenSCAD module for this path
		id = node.get ( 'id', '' )
		if ( id is None ) or ( id == '' ):
			id = str( self.pathid ) + 'x'
			self.pathid += 1
		self.f.write( 'module path_' + id + '(h)\n{\n' )

		# And add the call to the call list
		self.call_list.append( 'path_%s(%s);\n' % ( id, self.options.height ) )

		for subpath in self.paths[node]:

			# A new polygon for a new subpath
			poly = \
				'  scale([25.4/90, -25.4/90, 1])\n' + \
				'    linear_extrude(height=h)\n' + \
				'      polygon(points=['

			firstPoint = subpath[0]
			lastPoint = None
			for point in subpath:
				poly += '[%f,%f],' % ( ( point[0] - self.cx ), ( point[1] - self.cy ) )
				lastPoint = point

			# Close the path if it isn't already closed
			if ( firstPoint != lastPoint ):
				poly += '[%f,%f],' % ( ( firstPoint[0] - self.cx ), ( firstPoint[1] - self.cy ) )

			poly = poly[:-1]
			poly += ']);\n'
			self.f.write( poly )

		# End the module
		self.f.write( '}\n' )

	def recursivelyTraverseSvg( self, aNodeList,
		matCurrent=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
		parent_visibility='visible', find_bbox=True ):

		'''
		[ This too is largely lifted from eggbot.py ]

		Recursively walk the SVG document, building polygon vertex lists
		for each graphical element we support.

		Rendered SVG elements:
			<circle>, <ellipse>, <line>, <path>, <polygon>, <polyline>, <rect>

		Supported SVG elements:
			<group>, <use>

		Ignored SVG elements:
			<defs>, <eggbot>, <metadata>, <namedview>, <pattern>,
			processing directives

		All other SVG elements trigger an error (including <text>)
		'''

		for node in aNodeList:

			# Ignore invisible nodes
			v = node.get( 'visibility', parent_visibility )
			if v == 'inherit':
				v = parent_visibility
			if v == 'hidden' or v == 'collapse':
				pass

			# First apply the current matrix transform to this node's tranform
			matNew = simpletransform.composeTransform( matCurrent, simpletransform.parseTransform( node.get( "transform" ) ) )

			if node.tag == inkex.addNS( 'g', 'svg' ) or node.tag == 'g':

				self.recursivelyTraverseSvg( node, matNew, v, find_bbox )

			elif node.tag == inkex.addNS( 'use', 'svg' ) or node.tag == 'use':

				# A <use> element refers to another SVG element via an xlink:href="#blah"
				# attribute.  We will handle the element by doing an XPath search through
				# the document, looking for the element with the matching id="blah"
				# attribute.  We then recursively process that element after applying
				# any necessary (x,y) translation.
				#
				# Notes:
				#  1. We ignore the height and width attributes as they do not apply to
				#     path-like elements, and
				#  2. Even if the use element has visibility="hidden", SVG still calls
				#     for processing the referenced element.  The referenced element is
				#     hidden only if its visibility is "inherit" or "hidden".

				refid = node.get( inkex.addNS( 'href', 'xlink' ) )
				if not refid:
					pass

				# [1:] to ignore leading '#' in reference
				path = '//*[@id="%s"]' % refid[1:]
				refnode = node.xpath( path )
				if refnode:
					x = float( node.get( 'x', '0' ) )
					y = float( node.get( 'y', '0' ) )
					# Note: the transform has already been applied
					if ( x != 0 ) or (y != 0 ):
						matNew2 = composeTransform( matNew, parseTransform( 'translate(%f,%f)' % (x,y) ) )
					else:
						matNew2 = matNew
					v = node.get( 'visibility', v )
					self.recursivelyTraverseSvg( refnode, matNew2, v, find_bbox )

			elif node.tag == inkex.addNS( 'path', 'svg' ):

				path_data = node.get( 'd')
				if path_data:
					self.getPathVertices( path_data, node, matNew, find_bbox )

			elif node.tag == inkex.addNS( 'rect', 'svg' ) or node.tag == 'rect':

				# Manually transform
				#
				#    <rect x="X" y="Y" width="W" height="H"/>
				#
				# into
				#
				#    <path d="MX,Y lW,0 l0,H l-W,0 z"/>
				#
				# I.e., explicitly draw three sides of the rectangle and the
				# fourth side implicitly

				# Create a path with the outline of the rectangle
				x = float( node.get( 'x' ) )
				y = float( node.get( 'y' ) )
				if ( not x ) or ( not y ):
					pass
				w = float( node.get( 'width', '0' ) )
				h = float( node.get( 'height', '0' ) )
				a = []
				a.append( ['M ', [x, y]] )
				a.append( [' l ', [w, 0]] )
				a.append( [' l ', [0, h]] )
				a.append( [' l ', [-w, 0]] )
				a.append( [' Z', []] )
				self.getPathVertices( simplepath.formatPath( a ), node, matNew, find_bbox )

			elif node.tag == inkex.addNS( 'line', 'svg' ) or node.tag == 'line':

				# Convert
				#
				#   <line x1="X1" y1="Y1" x2="X2" y2="Y2/>
				#
				# to
				#
				#   <path d="MX1,Y1 LX2,Y2"/>

				x1 = float( node.get( 'x1' ) )
				y1 = float( node.get( 'y1' ) )
				x2 = float( node.get( 'x2' ) )
				y2 = float( node.get( 'y2' ) )
				if ( not x1 ) or ( not y1 ) or ( not x2 ) or ( not y2 ):
					pass
				a = []
				a.append( ['M ', [x1, y1]] )
				a.append( [' L ', [x2, y2]] )
				self.getPathVertices( simplepath.formatPath( a ), node, matNew, find_bbox )

			elif node.tag == inkex.addNS( 'polyline', 'svg' ) or node.tag == 'polyline':

				# Convert
				#
				#  <polyline points="x1,y1 x2,y2 x3,y3 [...]"/>
				#
				# to
				#
				#   <path d="Mx1,y1 Lx2,y2 Lx3,y3 [...]"/>
				#
				# Note: we ignore polylines with no points

				pl = node.get( 'points', '' ).strip()
				if pl == '':
					pass

				pa = pl.split()
				d = "".join( ["M " + pa[i] if i == 0 else " L " + pa[i] for i in range( 0, len( pa ) )] )
				self.getPathVertices( d, node, matNew, find_bbox )

			elif node.tag == inkex.addNS( 'polygon', 'svg' ) or node.tag == 'polygon':

				# Convert
				#
				#  <polygon points="x1,y1 x2,y2 x3,y3 [...]"/>
				#
				# to
				#
				#   <path d="Mx1,y1 Lx2,y2 Lx3,y3 [...] Z"/>
				#
				# Note: we ignore polygons with no points

				pl = node.get( 'points', '' ).strip()
				if pl == '':
					pass

				pa = pl.split()
				d = "".join( ["M " + pa[i] if i == 0 else " L " + pa[i] for i in range( 0, len( pa ) )] )
				d += " Z"
				self.getPathVertices( d, node, matNew, find_bbox )

			elif node.tag == inkex.addNS( 'ellipse', 'svg' ) or \
				node.tag == 'ellipse' or \
				node.tag == inkex.addNS( 'circle', 'svg' ) or \
				node.tag == 'circle':

					# Convert circles and ellipses to a path with two 180 degree arcs.
					# In general (an ellipse), we convert
					#
					#   <ellipse rx="RX" ry="RY" cx="X" cy="Y"/>
					#
					# to
					#
					#   <path d="MX1,CY A RX,RY 0 1 0 X2,CY A RX,RY 0 1 0 X1,CY"/>
					#
					# where
					#
					#   X1 = CX - RX
					#   X2 = CX + RX
					#
					# Note: ellipses or circles with a radius attribute of value 0 are ignored

					if node.tag == inkex.addNS( 'ellipse', 'svg' ) or node.tag == 'ellipse':
						rx = float( node.get( 'rx', '0' ) )
						ry = float( node.get( 'ry', '0' ) )
					else:
						rx = float( node.get( 'r', '0' ) )
						ry = rx
					if rx == 0 or ry == 0:
						pass

					cx = float( node.get( 'cx', '0' ) )
					cy = float( node.get( 'cy', '0' ) )
					x1 = cx - rx
					x2 = cx + rx
					d = 'M %f,%f ' % ( x1, cy ) + \
						'A %f,%f ' % ( rx, ry ) + \
						'0 1 0 %f,%f ' % ( x2, cy ) + \
						'A %f,%f ' % ( rx, ry ) + \
						'0 1 0 %f,%f' % ( x1, cy )
					self.mapPathVertices( d, node, matNew, find_bbox )

			elif node.tag == inkex.addNS( 'pattern', 'svg' ) or node.tag == 'pattern':

				pass

			elif node.tag == inkex.addNS( 'metadata', 'svg' ) or node.tag == 'metadata':

				pass

			elif node.tag == inkex.addNS( 'defs', 'svg' ) or node.tag == 'defs':

				pass

			elif node.tag == inkex.addNS( 'desc', 'svg' ) or node.tag == 'desc':

				pass

			elif node.tag == inkex.addNS( 'namedview', 'sodipodi' ) or node.tag == 'namedview':

				pass

			elif node.tag == inkex.addNS( 'eggbot', 'svg' ) or node.tag == 'eggbot':

				pass

			elif node.tag == inkex.addNS( 'text', 'svg' ) or node.tag == 'text':

				inkex.errormsg( 'Warning: unable to draw text, please convert it to a path first.' )

				pass

			elif node.tag == inkex.addNS( 'title', 'svg' ) or node.tag == 'title':

				pass

			elif node.tag == inkex.addNS( 'image', 'svg' ) or node.tag == 'image':

				if not self.warnings.has_key( 'image' ):
					inkex.errormsg( gettext.gettext( 'Warning: unable to draw bitmap images; ' +
						'please convert them to line art first.  Consider using the "Trace bitmap..." ' +
						'tool of the "Path" menu.  Mac users please note that some X11 settings may ' +
						'cause cut-and-paste operations to paste in bitmap copies.' ) )
					self.warnings['image'] = 1
				pass

			elif node.tag == inkex.addNS( 'pattern', 'svg' ) or node.tag == 'pattern':

				pass

			elif node.tag == inkex.addNS( 'radialGradient', 'svg' ) or node.tag == 'radialGradient':

				# Similar to pattern
				pass

			elif node.tag == inkex.addNS( 'linearGradient', 'svg' ) or node.tag == 'linearGradient':

				# Similar in pattern
				pass

			elif node.tag == inkex.addNS( 'style', 'svg' ) or node.tag == 'style':

				# This is a reference to an external style sheet and not the value
				# of a style attribute to be inherited by child elements
				pass

			elif node.tag == inkex.addNS( 'cursor', 'svg' ) or node.tag == 'cursor':

				pass

			elif node.tag == inkex.addNS( 'color-profile', 'svg' ) or node.tag == 'color-profile':

				# Gamma curves, color temp, etc. are not relevant to single color output
				pass

			elif not isinstance( node.tag, basestring ):

				# This is likely an XML processing instruction such as an XML
				# comment.  lxml uses a function reference for such node tags
				# and as such the node tag is likely not a printable string.
				# Further, converting it to a printable string likely won't
				# be very useful.

				pass

			else:

				inkex.errormsg( 'Warning: unable to draw object <%s>, please convert it to a path first.' % node.tag )
				pass


	def recursivelyGetEnclosingTransform( self, node ):

		'''
		Determine the cumulative transform which node inherits from
		its chain of ancestors.
		'''
		node = node.getparent()
		if node is not None:
			parent_transform = self.recursivelyGetEnclosingTransform( node )
			node_transform = node.get( 'transform', None )
			if node_transform is None:
				return parent_transform
			else:
				tr = simpletransform.parseTransform( node_transform )
				if parent_transform is None:
					return tr
				else:
					return simpletransform.composeTransform( parent_transform, tr )
		else:
			return self.docTransform

	def effect( self ):

		# Viewbox handling
		self.handleViewBox()

		# First traverse the document (or selected items), reducing
		# everything to line segments.  If working on a selection,
		# then determine the selection's bounding box in the process.
		# (Actually, we just need to know it's extrema on the x-axis.)

		if self.options.ids:
			# Traverse the selected objects
			for id in self.options.ids:
				transform = self.recursivelyGetEnclosingTransform( self.selected[id] )
				self.recursivelyTraverseSvg( [self.selected[id]], transform, find_bbox=True )
		else:
			# Traverse the entire document building new, transformed paths
			self.recursivelyTraverseSvg( self.document.getroot(), self.docTransform, find_bbox=True )

		self.cx = self.xmin + ( self.xmax - self.xmin ) / 2.0
		self.cy = self.ymin + ( self.ymax - self.ymin ) / 2.0

		try:
			self.f = open( os.path.expanduser( self.options.fname ), 'w')

			# Now that we know the x-axis extrema, we can remap the data
			# Had we know the x-axis extrema in advance (i.e., operating
			# on the entire document), then we could have done the mapping
			# at the same time we "rendered" everything to line segments.

			for key in self.paths:
				self.f.write( '\n' )
				self.convertPath( key )

			# Now output the list of modules to call
			self.f.write( '\n' )
			for call in self.call_list:
				self.f.write( call )

		except:
			inkex.errormsg( 'Unable to open the file ' + self.options.fname )

if __name__ == '__main__':

	e = OpenSCAD()
	e.affect()
