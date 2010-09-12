#! /usr/bin/env python
# -*- coding: utf-8 -*-

from optparse import OptionParser, OptionGroup
import ConfigParser
import datetime
import fnmatch
import glob
import logging
import os
import re
import string
import shlex
import shutil
import subprocess
import sys

#import fileinput
import threading
import traceback


#SCRIPT_VERSION = "3.00"
SCRIPT_VERSION = "2.02"
#SCRIPT_NAME = "Eagle3D INC SRC Compiler v%s"%SCRIPT_VERSION
SCRIPT_NAME = "INC SRC Compiler v%s"%SCRIPT_VERSION


###############################################################################
#
def upDir(path, levels=1):
	if levels == 1:
		return os.path.dirname(path)
	elif levels > 1:
		return upDir(os.path.dirname(path), levels-1)
	else:
		return None


###############################################################################
#
def which(program):
	import os
	def is_exe(fpath):
		#print fpath+': '+str(os.path.isfile(fpath))+', '+str(os.access(fpath, os.X_OK))
		return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

	fpath, fname = os.path.split(program)
	if fpath:
		if is_exe(program):
			return program
	else:
		for path in os.environ.get("PATH", '').split(os.pathsep):
			exe_file = os.path.join(path, program)
			if is_exe(exe_file):
				return exe_file

	return False


###############################################################################
#
def touch(filepath):
	if not os.path.exists(filepath):
		open(filepath, 'w').close()


###############################################################################
#
def subprocess_call(command, cwd=None):
	NULL = open('/dev/null', 'w')
	return subprocess.call(command, cwd=cwd, stdout=NULL, stderr=NULL)


########################################
#
"""
*.src.inc format:
Part name (Currently ignored, only one line)
//Comments inserted before the macro (always prefixed by "//")
################################################################
Lines for the 3dpack.dat
################################################################
Main macro name (without parameter list, see naming conventions above)
Main macro parameter list
################################################################
//Comment for the sub macro (optional)
Sub macro name(parameter list)
Parameter list for main macro
################################################################
################################################################
Actual macro


"""
def parse_inc_src(inc_src_content=[], inc_src_block=0):
	result = []
	inc_src_content_len = len(inc_src_content)
	inc_src_index = 1
	inc_src_block_index = 0
	while inc_src_index<inc_src_content_len and inc_src_block_index != inc_src_block:
		if inc_src_content[inc_src_index][:20] == "#"*20:
			inc_src_block_index = inc_src_block_index+1
		inc_src_index = inc_src_index+1
	while inc_src_index<inc_src_content_len and inc_src_content[inc_src_index][:20] != "#"*20:
		result.append(inc_src_content[inc_src_index])
		inc_src_index = inc_src_index+1
	return result


###############################################################################
#
class Callable:
	def __init__(self, _callable):
		self.__call__ = _callable


###############################################################################
#
class ProcessQueue(threading.Thread):
	logger = None
	keep_alive = None
	process_list = None
	title_list = None
	_dev_null = None

	def __init__ (self, max_proc=4, logger=None):
		self.logger = logger
		self.process_list = []
		self.title_list = []
		for i in range(0, max_proc):
			self.process_list.append(None)
			self.title_list.append(None)
		self._dev_null = open('/dev/null', 'w')
		threading.Thread.__init__(self)

	def kill(self):
		self.keep_alive = False
		for i in range(0, len(self.process_list)):
			if self.process_list[i] != None:
				try:
					self.process_list[i].kill()
				except OSError:
					pass

	def wait(self):
		any_alive = True
		try:
			while any_alive:
				any_alive = False
				for i in range(0, len(self.process_list)):
					if self.process_list[i] != None:
						any_alive = True
			self.keep_alive = False
		except KeyboardInterrupt:
			self.kill()
			return

	def run(self):
		self.keep_alive = True
		try:
			while self.keep_alive:
				for i in range(0, len(self.process_list)):
					# Popen.poll() returns None if still alive
					if self.process_list[i] != None:
						exitcode = self.process_list[i].poll()
						if exitcode != None:
							if self.logger != None:
								logger.info("%s done, exit code %d."%(self.title_list[i], self.process_list[i].poll()))
								#stderr = self.process_list[i].communicate()[1]
								#if exitcode != 0:
									#logger.info("stderr: %s"%(stderr))
							self.process_list[i] = None
							self.title_list[i] = None
		except KeyboardInterrupt:
			self.kill()
			return

	def add_process(self, command, title=None):
		if title == None:
			title = command
		handled = False
		while not handled:
			for i in range(0, len(self.process_list)):
				if self.process_list[i] == None:
					try:
						#self.process_list[i] = subprocess.Popen(shlex.split(command), stdout=self._dev_null, stderr=subprocess.PIPE, universal_newlines=True)
						self.process_list[i] = subprocess.Popen(shlex.split(command), stdout=self._dev_null, stderr=self._dev_null, universal_newlines=True)
						self.title_list[i] = title
						if self.logger != None:
							logger.info("%s queued."%(title))
					except IOError, e:
						if self.logger != None:
							logger.info("%s exited with I/O error %d: %s."%(title, e[0], e[1]))
							logger.info("command: %s"%(command))
						self.process_list[i] = None
					except OSError, e:
						if self.logger != None:
							logger.info("%s exited with OS error %d: %s."%(title, e[0], e[1]))
							logger.info("command: %s"%(command))
						self.process_list[i] = None
					except KeyboardInterrupt:
						self.kill()
						return
					handled = True
					return


###############################################################################
#
class HtmlFileWriter():

	files = {}
	header_string = ""
	footer_string = ""
	max_rowcount = 0
	max_colcount = 0
	quite = True

	def __init__(self, cols, rows, quiet=True):
		self.max_colcount = cols
		self.max_rowcount = rows
		self.quiet = quiet

	def set_header_string(self, header_string):
		self.header_string = header_string

	def write_header(self, k, title_string=""):
		if not k in self.files:
			self.files[k] = {'filepath':k%int(0), 'rowcount':0, 'colcount':0, 'page':0, 'title':title_string}
			if not self.quiet: logger.info('adding file: '+self.files[k]['filepath'])

		if not os.path.exists(self.files[k]['filepath']):
			f = open(self.files[k]['filepath'], 'w')
			if '%TITLE%' in self.header_string:
				f.write(self.header_string.replace('%TITLE%', self.files[k]['title']))
			else:
				f.write(self.header_string)
			f.close()

	def write_body(self, k, content):

		if self.files[k]['colcount'] == self.max_colcount:
			self.files[k]['colcount'] = 0
			self.files[k]['rowcount'] = self.files[k]['rowcount'] + 1
			if self.files[k]['rowcount'] == self.max_rowcount:
				self.files[k]['rowcount'] = 0
				self.files[k]['page'] = self.files[k]['page'] + 1
				self.files[k]['filepath'] = k%self.files[k]['page']
				self.write_header(k)
			else:
				f = open(self.files[k]['filepath'], 'a')
				f.write("\t</tr>\n")
				f.write("\t<tr>\n")
				f.close()

		f = open(self.files[k]['filepath'], 'a')
		f.write(content)
		f.close()
		self.files[k]['colcount'] = self.files[k]['colcount'] + 1

	def set_footer_string(self, footer_string):
		self.footer_string = footer_string

	def get_page_links(self, k, on_page=None):
		links = []
		if on_page != None:
			if on_page > 0:
				href = os.path.basename(k%(on_page-1))
				links.append('<a href="%s" title="back">&lt;&lt;&lt;</a>'%(href))
			else:
				links.append('&lt;&lt;&lt;')
		for page in range(0, self.files[k]['page']+1):
			if on_page != page:
				href = os.path.basename(k%page)
				links.append('<a href="%s" title="page %d">.%d.</a>'%(href, page, page))
			else:
				links.append('.%d.'%(page))
		if on_page != None:
			if on_page < page:
				href = os.path.basename(k%(on_page+1))
				links.append('<a href="%s" title="next">&gt;&gt;&gt;</a>'%(href))
			else:
				links.append('&gt;&gt;&gt;')
		return ''.join(links)

	def write_footer(self, k, page=None):
		if page == None:
			if not self.quiet: logger.info('finalizing file: '+self.files[k]['filepath'])
			f = open(self.files[k]['filepath'], 'a')
		else:
			if not self.quiet: logger.info('finalizing file: '+k%page)
			f = open(k%page, 'a')
		if '%PAGE_LINKS%' in self.footer_string:
			f.write(self.footer_string.replace('%PAGE_LINKS%', self.get_page_links(k, page)))
		else:
			f.write(self.footer_string)
		f.close()

	def write_all_footers(self):
		if len(self.files) > 0:
			for k in self.files.keys():
				for page in range(0, self.files[k]['page']+1):
					self.write_footer(k, page)


###############################################################################
#
class _ConfigParser(ConfigParser.SafeConfigParser):

	filepath = None

	def __init__(self):
		self.filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'eagle3d.conf')
		ConfigParser.SafeConfigParser.__init__(self)

	def config_exists(self):
		return os.path.exists(self.filepath)

	def dump(self, section=None):
		for s in self.sections():
			if section==None or (section and s==section):
				print "["+s+"]"
				for o in self.options(s):
					print "  %s = %s"%(o, self.get(s, o))

	def convert(self, item):
		itype = item[:2]
		ivalue = item[2:]
		if itype == 's:':
			item = ivalue
		elif itype == 'b:':
			if ivalue.upper() in ['TRUE','YES','ON','1']:
				item = True
			else:
				item = False
		elif itype == 'i:':
			item = int(ivalue)
		elif itype == 'f:':
			item = float(ivalue)
		elif itype == 'l:':
			item = long(ivalue)
		elif itype == 'n:':
			item = None
		else:
			pass
		return item

	def _get(self, key):
		if self.has_option('general', key):
			if key.endswith('_list'):
				items = []
				for i in self.convert(self.get('general', key)).split(','):
					items.append(i.strip())
				return items
			else:
				return self.convert(self.get('general', key))
		elif self.has_section(key):
			if key.endswith('_map'):
				items = {}
				for i in self.items(key):
					items[i[0]] = i[1]
				return items
			else:
				return self.items(key)
		raise ConfigParser.NoOptionError

	def _getbin(self, name):
		if self.has_option('system', name):
			return self.get('system', name)
		raise ConfigParser.NoOptionError

	def update_options(self, options, defaults=None):
		for opt in options.__dict__:
			if opt in ['help', 'rewrite_config', 'recheck_config']:
				continue
			value = options.__dict__[opt]
			#ignore the option if it is the default,
			#  this means it was was not set or it was set to the same value
			if defaults != None and value == defaults.__dict__[opt]:
				continue
			if isinstance(value, str):
				prefix = 's:'
			elif isinstance(value, bool):
				prefix = 'b:'
			elif isinstance(value, int):
				prefix = 'i:'
			elif isinstance(value, float):
				prefix = 'f:'
			elif isinstance(value, long):
				prefix = 'l:'
			elif value == None:
				prefix = 'n:'
			else:
				prefix = 'u:'
			config.set('general', opt, prefix+str(value))

	def init_config(self):
		self.set_boilerplate_config()
		self.set_system_config()

	def read_config(self):
		if self.config_exists():
			self.read(self.filepath)
		else:
			self.init_config()

	def write_config(self):
		pfile = open(self.filepath, 'wb')
		self.write(pfile)
		pfile.close()

	def set_boilerplate_config(self):

		section = 'general'
		if not self.has_section(section):
			self.add_section(section)
		self.set(section, 'src_inc_file_ignore_list',         's:pre.pre, pos.pos')
		self.set(section, 'src_inc_dir_ignore_list',          's:.*')
		self.set(section, 'src_inc_extension',                's:.inc.src')
		self.set(section, 'inc_extension',                    's:.inc')
		self.set(section, 'img_extension',                    's:.png')
		self.set(section, 'render_extension',                 's:.pov')
		self.set(section, 'src_inc_suffix',                   's:_GRND')

		section = 'src_inc_prefix_map'
		if not self.has_section(section):
			self.add_section(section)
		self.set(section, 'cap',                              'CAP_')
		self.set(section, 'capwima',                          'CAP_')
		self.set(section, 'connector',                        'CON_')
		self.set(section, 'diode',                            'DIODE_')
		self.set(section, 'ic',                               'IC_')
		self.set(section, 'qfp',                              'QFP_')
		self.set(section, 'resistor',                         'RES_')
		self.set(section, 'socket',                           'SOCKET_')
		self.set(section, 'special',                          'SPC_')
		self.set(section, 'switch',                           'SWITCH_')
		self.set(section, 'trafo',                            'TRAFO_')
		self.set(section, 'transistor',                       'TR_')

		section = 'macro_parameter_pattern_map'
		if not self.has_section(section):
			self.add_section(section)
		self.set(section, 'value,col,tra,height',             '"POV",Red,0.7,0')
		self.set(section, 'col,tra,height',                   'Red,0.7,0')
		self.set(section, 'col,tra',                          'Red,0.7')
		self.set(section, 'color_sub',                        'DarkWood')
		self.set(section, 'value,logo',                       '"POV",""')
		self.set(section, 'value,height',                     '"POV",3')
		self.set(section, 'value',                            '"POV"')
		self.set(section, 'name,logo',                        '"POV",""')
		self.set(section, 'name',                             '"POV"')
		self.set(section, 'height',                           '3')
		self.set(section, 'c1,c2,c3,c4',                      'texture{pigment{Yellow}finish{phong 0.2}},texture{pigment{Violet*1.2}finish{phong 0.2}},texture{pigment{Red*0.7}finish{phong 0.2}},texture {T_Gold_5C finish{reflection 0.1}}')
		self.set(section, 'j',                                '1')

	def set_system_config(self):

		section = 'system'
		if not self.has_section(section):
			self.add_section(section)
		self.set(section, 'zip',                              str(which('zip')))
		self.set(section, 'tar',                              str(which('tar')))
		self.set(section, 'gzip',                             str(which('gzip')))
		self.set(section, 'bzip2',                            str(which('bzip2')))
		self.set(section, 'todos',                            str(which('todos')))
		self.set(section, 'unix2dos',                         str(which('unix2dos')))
		self.set(section, 'dos2unix',                         str(which('dos2unix')))
		self.set(section, 'makensis',                         str(which('makensis')))
		self.set(section, 'povray',                           str(which('povray')))
		self.set(section, 'nice',                             str(which('nice')))
		self.set(section, 'convert',                          str(which('convert')))
		self.set(section, 'montage',                          str(which('montage')))



###############################################################################
#
class env:
	WORKDIR = None
	SCRIPTDIR = None
	ARCHIVE_OUTPUT_DIR = None

	SRCDIR_ROOT = None
	SRCDIR_DATA = None
	SRCDIR_DOC = None
	SRCDIR_EXAMPLES = None
	SRCDIR_INC = None
	SRCDIR_ULP = None

	OUTDIR_ROOT = None
	OUTDIR_3DPACK = None
	OUTDIR_INC = None
	OUTDIR_POV = None
	OUTDIR_IMG = None

	RELEASEDIR = None
	RELEASEDIR_ULP = None
	RELEASEDIR_POVRAY = None
	RELEASEDIR_ULP = None
	RELEASEDIR_EXAMPLES = None

	def init():
		#get the directory we are in currently
		env.WORKDIR = os.getcwd()
		#get the directory this script is in
		env.SCRIPTDIR = os.path.dirname(os.path.abspath(__file__))

		#is the working directory the tools directory?
		if env.WORKDIR == env.SCRIPTDIR:
			env.ARCHIVE_OUTPUT_DIR = upDir(env.WORKDIR)

			env.SRCDIR_ROOT = os.path.join(upDir(env.WORKDIR),'src')
			env.SRCDIR_DATA = os.path.join(env.SRCDIR_ROOT,'data')
			env.SRCDIR_DOC = os.path.join(env.SRCDIR_ROOT,'doc')
			env.SRCDIR_EXAMPLES = os.path.join(env.SRCDIR_ROOT,'examples')
			env.SRCDIR_INC = os.path.join(env.SRCDIR_ROOT,'inc')
			env.SRCDIR_ULP = os.path.join(env.SRCDIR_ROOT,'ulp')

			env.OUTDIR_ROOT = os.path.join(upDir(env.WORKDIR),'build')
			env.OUTDIR_3DPACK = os.path.join(env.OUTDIR_ROOT, "3dpack")
			env.OUTDIR_INC = os.path.join(env.OUTDIR_ROOT, "inc")
			env.OUTDIR_POV = os.path.join(env.OUTDIR_ROOT, "pov")
			env.OUTDIR_IMG = os.path.join(env.OUTDIR_ROOT, "img")

			env.RELEASEDIR = os.path.join(env.OUTDIR_ROOT,'eagle3d')
			env.RELEASEDIR_ULP = os.path.join(env.RELEASEDIR,'ulp')
			env.RELEASEDIR_POVRAY = os.path.join(env.RELEASEDIR,'povray')
			env.RELEASEDIR_ULP = os.path.join(env.RELEASEDIR,'doc')
			env.RELEASEDIR_EXAMPLES = os.path.join(env.RELEASEDIR,'examples')

		#is the working directory one level up from tools?
		elif os.path.isdir(os.path.join(env.WORKDIR,'src')) and os.path.isdir(os.path.join(env.WORKDIR,'tools')):
			env.ARCHIVE_OUTPUT_DIR = env.WORKDIR

			env.SRCDIR_ROOT = os.path.join(env.WORKDIR,'src')
			env.SRCDIR_DATA = os.path.join(env.SRCDIR_ROOT,'data')
			env.SRCDIR_DOC = os.path.join(env.SRCDIR_ROOT,'doc')
			env.SRCDIR_EXAMPLES = os.path.join(env.SRCDIR_ROOT,'examples')
			env.SRCDIR_INC = os.path.join(env.SRCDIR_ROOT,'inc')
			env.SRCDIR_ULP = os.path.join(env.SRCDIR_ROOT,'ulp')

			env.OUTDIR_ROOT = os.path.join(env.WORKDIR,'build')
			env.OUTDIR_3DPACK = os.path.join(env.OUTDIR_ROOT, "3dpack")
			env.OUTDIR_INC = os.path.join(env.OUTDIR_ROOT, "inc")
			env.OUTDIR_POV = os.path.join(env.OUTDIR_ROOT, "pov")
			env.OUTDIR_IMG = os.path.join(env.OUTDIR_ROOT, "img")

			env.RELEASEDIR = os.path.join(env.OUTDIR_ROOT,'eagle3d')
			env.RELEASEDIR_ULP = os.path.join(env.RELEASEDIR,'ulp')
			env.RELEASEDIR_POVRAY = os.path.join(env.RELEASEDIR,'povray')
			env.RELEASEDIR_DOC = os.path.join(env.RELEASEDIR,'doc')
			env.RELEASEDIR_EXAMPLES = os.path.join(env.RELEASEDIR,'examples')

		else:
			WORKDIR = None
			SCRIPTDIR = None
			#echo "Script run from invalid position."
			#echo "Start it from the root of the Eagle3D source or from the tools/ dir."
			#exit

	init = Callable(init)

	def dump():
		print "PATHS:"
		print "  WORKDIR: %s"%env.WORKDIR
		print "  SCRIPTDIR: %s"%env.SCRIPTDIR
		print "  ARCHIVE_OUTPUT_DIR: %s"%env.ARCHIVE_OUTPUT_DIR
		print "  SRCDIR_ROOT: %s"%env.SRCDIR_ROOT
		print "  SRCDIR_DATA: %s"%env.SRCDIR_DATA
		print "  SRCDIR_DOC: %s"%env.SRCDIR_DOC
		print "  SRCDIR_EXAMPLES: %s"%env.SRCDIR_EXAMPLES
		print "  SRCDIR_INC: %s"%env.SRCDIR_INC
		print "  SRCDIR_ULP: %s"%env.SRCDIR_ULP
		print "  OUTDIR_ROOT: %s"%env.OUTDIR_ROOT
		print "  OUTDIR_3DPACK: %s"%env.OUTDIR_3DPACK
		print "  OUTDIR_INC: %s"%env.OUTDIR_INC
		print "  OUTDIR_POV: %s"%env.OUTDIR_POV
		print "  OUTDIR_IMG: %s"%env.OUTDIR_IMG
		print "  RELEASEDIR: %s"%env.RELEASEDIR
		print "  RELEASEDIR_ULP: %s"%env.RELEASEDIR_ULP
		print "  RELEASEDIR_POVRAY: %s"%env.RELEASEDIR_POVRAY
		print "  RELEASEDIR_ULP: %s"%env.RELEASEDIR_ULP
		print "  RELEASEDIR_EXAMPLES: %s"%env.RELEASEDIR_EXAMPLES

	dump = Callable(dump)


class iterate_dir(object):

	def __init__(self, quiet):
		self.quiet = quiet
		object.__init__(self)

	def on_each_rootdir_pre(self, rootdir):
		pass

	def on_each_rootdir_post(self, rootdir):
		pass

	#def on_each_file(self, rootdir, file):
	def on_each_file(self, filepath):
		pass

	def start(self, topdir):
		for rootdir, dirlist, filelist in os.walk(topdir):
			rootdir_basename = os.path.basename(rootdir)

			skip_dir = False
			for pattern in config._get('src_inc_dir_ignore_list'):
				if fnmatch.fnmatch(rootdir_basename, pattern):
					#do not recurse
					dirlist[:]=[]
					#skip this directory, continue will just skip to the next pattern
					skip_dir = True

			if skip_dir:
				continue

			# skip the base directory
			if rootdir == topdir:
				continue

			dirlist.sort()

			self.on_each_rootdir_pre(rootdir)

			filelist.sort()
			for f in filelist:
				#ignore files
				if f in config._get('src_inc_file_ignore_list'):
					continue

				#self.on_each_file(rootdir, f)
				self.on_each_file(os.path.join(rootdir, f))

			self.on_each_rootdir_post(rootdir)


###############################################################################
#
class _Worker:

	version = None
	timestamp = None

	###############################################################################
	# return the time and date in the format of: 22.08.2010 22:31:52
	def formatted_datetime(self):
		if not self.timestamp:
			self.timestamp = datetime.datetime.now()
		return self.timestamp.strftime('%d.%m.%Y %H:%M:%S')


	########################################
	#
	def version_to_filename(version):
		if not self.timestamp:
			self.timestamp = datetime.datetime.now()
		filename = version.replace(' ', '_').replace('.', '_')
		filename = filename + self.timestamp.strftime('_%d%m%G')
		return filename


	########################################
	#
	def verify(self):
		verify_mask = config._get('verify_mask')
		verify_full_check = config._get('verify_full_check')
		quiet = config._get('quiet')

		all_errors_found = 0

		####################
		#
		logger.info("collecting macro names...")
		class iterate_dir1(iterate_dir):
			all_errors_found = 0
			all_inc_macros = []
			def on_each_file(self, filepath):
				filepath_basename = os.path.basename(filepath)

				if not fnmatch.fnmatch(filepath_basename, verify_mask):
					return

				errors_found = 0

				filepath_subdir = os.path.basename(os.path.dirname(filepath))
				filepath_rel = os.path.join(filepath_subdir, filepath_basename)

				f_inc_src = open(filepath, 'r')
				content = f_inc_src.read()
				f_inc_src.close()
				content = content.split("\n")
				index = 0

				for i in parse_inc_src(content, 3):
					if i.strip() == '' or i[:2] == "//" or i[:1] == "(":
						pass
					else:
						#split on left paren to get the macro name only
						i_split = i.split('(')
						if len(i_split) > 1:
							self.all_inc_macros.append(i_split[0].strip())
						else:
							logger.info("ERROR: "+filepath+'; line: '+i)
							errors_found = errors_found+1

				if not quiet and errors_found<1:
					logger.info(filepath_rel+': no errors found.')

				if errors_found:
					self.all_errors_found = self.all_errors_found+errors_found

		it = iterate_dir1(quiet)
		it.start(env.SRCDIR_INC)

		logger.info("total of %s macros"%(str(len(it.all_inc_macros))))
		logger.info('')


		logger.info("checking defined macros against used macros...")

		f_3dpack = open(os.path.join(env.OUTDIR_3DPACK, "3dpack.dat"), 'r')
		f_3dpack_content = f_3dpack.readlines()
		f_3dpack.close()

		f_3dpack_macros = []
		for i in f_3dpack_content:
			i_split = i.split(':')
			if len(i_split) > 30:
				f_3dpack_macros.append(i_split[31][:-1])
			else:
				logger.info("ERROR: 3dpack.dat; "+i)

		missing_inc_macros_count = 0
		missing_inc_macros = []
		for i in it.all_inc_macros:
			if i not in f_3dpack_macros:
				missing_inc_macros_count = missing_inc_macros_count+1
				missing_inc_macros.append(i)

		if missing_inc_macros_count>0:
			logger.info("found %s defined parts that are not in 3dpack.dat:"%(str(missing_inc_macros_count)))
			for i in missing_inc_macros:
				logger.info("%s"%(i))
		else:
			logger.info('no unused parts found.')
		logger.info('')

		all_errors_found = all_errors_found+missing_inc_macros_count


		####################
		#
		logger.info("checking macro include file format...")
		class iterate_dir2(iterate_dir):
			all_errors_found = 0

			src_inc_extension = config._get('src_inc_extension')
			src_inc_prefix_map = config._get('src_inc_prefix_map')
			src_inc_suffix = config._get('src_inc_suffix')

			def on_each_file(self, filepath):
				filepath_basename = os.path.basename(filepath)

				if not fnmatch.fnmatch(filepath_basename, verify_mask):
					return

				errors_found = 0

				filepath_subdir = os.path.basename(os.path.dirname(filepath))
				#filepath_rel = filepath_subdir+filepath[len(rootdir)-1:]
				filepath_rel = os.path.join(filepath_subdir, filepath_basename)

				filepath_barename = ''
				if filepath.endswith(self.src_inc_extension):
					filepath_barename = filepath_basename[:-len(self.src_inc_extension)]

				if not filepath_barename.startswith(self.src_inc_prefix_map[filepath_subdir]):
					logger.info(filepath_rel+': file name is inconsistant with naming rules, expected prefix %s.'%self.src_inc_prefix_map[filepath_subdir])
					errors_found = errors_found+1

				if not filepath_barename.endswith(self.src_inc_suffix):
					logger.info(filepath_rel+': file name is inconsistant with naming rules, expected suffix %s.'%self.src_inc_suffix)
					errors_found = errors_found+1

				f_inc_src = open(filepath, 'r')
				content = f_inc_src.read()
				f_inc_src.close()
				content = content.split("\n")
				index = 0

				for i in parse_inc_src(content, 2):
					if i.strip() == '' or i[:2] == "//" or i[:1] == "(":
						pass
					else:
						if i.strip() != filepath_barename:
							logger.info(filepath_rel+': main macro "%s" is inconsistant with naming rules, should match file barename: %s.'%(i, filepath_barename))
							errors_found = errors_found+1

				if verify_full_check:
					for i in parse_inc_src(content, 3):
						if i.strip() == '' or i[:2] == "//" or i[:1] == "(":
							pass
						else:
							#split on left paren to get the macro name only
							i_split = i.split('(')
							if len(i_split) > 1:
								sub_macro = i_split[0].strip()
								if not sub_macro.startswith(self.src_inc_prefix_map[filepath_subdir]):
									logger.info(filepath_rel+': sub macro "%s" is inconsistant with naming rules, expected prefix %s.'%(sub_macro, self.src_inc_prefix_map[filepath_subdir]))
							else:
								logger.info("ERROR: "+filepath+'; line: '+i)

				if not quiet and errors_found<1:
					logger.info(filepath_rel+': no errors found')

				if errors_found != None:
					self.all_errors_found = self.all_errors_found+errors_found

		it = iterate_dir2(quiet)
		it.start(env.SRCDIR_INC)
		if it.all_errors_found == 0:
			logger.info("no errors found")
		logger.info('')

		return all_errors_found

	#verify = Callable(verify)


	########################################
	#
	def clean(self):
		quiet = config._get('quiet')

		########################################
		# remove intermediate files
		if os.path.exists(env.OUTDIR_ROOT):
			if os.path.isdir(env.OUTDIR_ROOT):
				try:
					shutil.rmtree(env.OUTDIR_ROOT)
				except shutil.Error:
					pass
			else:
				return 1
		if os.path.exists(env.ARCHIVE_OUTPUT_DIR):
			for filepath in glob.glob(os.path.join(env.ARCHIVE_OUTPUT_DIR, "eagle3d*.zip")):
				os.remove(filepath)
			for filepath in glob.glob(os.path.join(env.ARCHIVE_OUTPUT_DIR, "eagle3d*.tar.gz")):
				os.remove(filepath)
			for filepath in glob.glob(os.path.join(env.ARCHIVE_OUTPUT_DIR, "eagle3d*.tar.bz2")):
				os.remove(filepath)
			for filepath in glob.glob(os.path.join(env.ARCHIVE_OUTPUT_DIR, "partSize.dat")):
				os.remove(filepath)

	#clean = Callable(clean)


	########################################
	#
	def create(self):
		quiet = config._get('quiet')
		create_mask = config._get('create_mask')
		self.clean()

		total_errors = 0

		logger.info('creating output directories...')
		os.makedirs(env.OUTDIR_ROOT)

		os.makedirs(env.OUTDIR_3DPACK)
		os.makedirs(env.OUTDIR_INC)
		os.makedirs(env.OUTDIR_POV)

		logger.info('creating library files...')
		f_3dpack = open(os.path.join(env.OUTDIR_3DPACK, "3dpack.dat"), 'w')

		class iterate_dir1(iterate_dir):
			macro_parameter_pattern_map = config._get('macro_parameter_pattern_map')

			def on_each_rootdir_pre(self, rootdir):
				rootdir_basename = os.path.basename(rootdir)

				# generate the output filename and open it for writing
				f_inc_filepath = os.path.join(env.OUTDIR_INC, "e3d_"+rootdir_basename)+".inc"
				if not quiet: logger.info("writing header for "+"e3d_"+rootdir_basename+".inc")
				f_inc = open(f_inc_filepath, 'w')

				# write file header
				f_inc.write("//Eagle3D ###VERSIONDUMMY### INC-File %s\n"%(os.path.basename(f_inc_filepath)))
				f_inc.write("//created by: %s\n"%(SCRIPT_NAME))
				f_inc.write("//created on: %s\n"%(worker.formatted_datetime()))
				f_inc.write("//(c) 2002-2010 by M. Weisser\n")
				f_inc.write("//or the author of the macro\n")
				f_inc.write("\n")

				# include global .pre file
				f_global_inc_pre = open(os.path.join(env.SRCDIR_DATA, "pre.pre"), 'r')
				f_inc.write(f_global_inc_pre.read())
				f_global_inc_pre.close()

				# include local .pre file
				f_local_inc_pre = open(os.path.join(env.SRCDIR_INC, rootdir_basename, "pre.pre"), 'r')
				f_inc.write(f_local_inc_pre.read())
				f_local_inc_pre.close()

				f_inc.close()

			def on_each_file(self, filepath):
				filepath_basename = os.path.basename(filepath)

				if not fnmatch.fnmatch(filepath_basename, create_mask):
					return

				filepath_subdir = os.path.basename(os.path.dirname(filepath))
				filepath_rel = os.path.join(filepath_subdir, filepath_basename)

				# generate the output filename and open it for writing (append)
				f_inc_filepath = os.path.join(env.OUTDIR_INC, "e3d_"+filepath_subdir)+".inc"
				f_inc = open(f_inc_filepath, 'a')

				if not quiet: logger.info("processing "+filepath_rel)

				# load the source file
				f_content = open(filepath, 'r')
				content = f_content.read()
				f_content.close()
				content = content.split("\n")
				#f_inc_src_index = 0

				# get the main macro name and argument list
				mainmacro = parse_inc_src(content, 2)

				# print the comments
				f_inc.write("/********************************************************************************************************************************************\n")
				for i in parse_inc_src(content, 0):
					f_inc.write(i)
					f_inc.write("\n")
				f_inc.write("********************************************************************************************************************************************/\n")

				# print the macro header
				f_inc.write("#macro ")
				f_inc.write(mainmacro[0])
				f_inc.write(mainmacro[1])
				f_inc.write("\n")

				# print the main macro body
				for i in parse_inc_src(content, 5):
					f_inc.write(i)
					f_inc.write("\n")

				# print the macro calls
				for i in parse_inc_src(content, 3):
					if i.strip() == '':
						pass
					elif i[:2] == "//":
						f_inc.write(i)
						f_inc.write("\n")
					elif i[:1] == "(":
						f_inc.write(mainmacro[0])
						f_inc.write(i)
						f_inc.write("\n#end\n")
					else:
						f_inc.write("#macro ")
						f_inc.write(i)
						f_inc.write("\n")

				f_inc.write("\n\n")
				f_inc.close()

				####################
				# append the 3dpack.dat file
				for i in parse_inc_src(content, 1):
					f_3dpack.write(i)
					f_3dpack.write("\n")

				####################
				# build the povray files
				macro_list = []
				for i in parse_inc_src(content, 3):
					if i.strip() == '' or i[:2] == "//" or i[:1] == "(":
						pass
					else:
						i_split = i.split('(')

						if len(i_split)>1:
							i_split[1] = i_split[1].strip()
							if i_split[1] == ')':
								i_split = i_split[:-1]
							else:
								i_split[1] = i_split[1][:-1].strip().replace(' ', '')
						macro_list.append(i_split)

				for macro in macro_list:
					if macro[0] == '':
						continue
					matched = 0
					if len(macro)>1:
						for pattern in self.macro_parameter_pattern_map:
							if macro[1] == pattern:
								macro[1] = self.macro_parameter_pattern_map[pattern]
								matched = matched+1
						if matched <1:
							logger.info("ERROR, unmatched argument string: "+macro[0]+'('+macro[1]+')')
						elif matched >1:
							logger.info("ERROR, argument string matched more than once: "+macro[0]+'('+macro[1]+')')

					# open the matching POV file
					f_pov = open(os.path.join(env.OUTDIR_POV, macro[0]+'.pov'), 'w')

					f_pov.write('//POVRay test file for macro %s\n'%(macro[0]))
					f_pov.write('//created by: %s\n'%(SCRIPT_NAME))
					f_pov.write('//created on: %s\n'%(worker.formatted_datetime()))
					f_pov.write('//(c) 2002-2010 by M. Weisser\n')
					f_pov.write('\n')
					f_pov.write('#include "povpre.pov"\n')
					f_pov.write('#local macroname = "%s"\n'%(macro[0]))
					f_pov.write('\n')

					if len(macro)>1:
						f_pov.write('#local obj = object{%s(%s)}\n'%(macro[0], macro[1]))
					else:
						f_pov.write('#local obj = object{%s()}\n'%(macro[0]))

					f_pov.write('#local x_size = (max_extent(obj) - min_extent(obj)).x;\n')
					f_pov.write('#local y_size = (max_extent(obj) - min_extent(obj)).y;\n')
					f_pov.write('#local z_size = (max_extent(obj) - min_extent(obj)).z;\n')
					f_pov.write('#local scale_f = 2/max(x_size,y_size,z_size);\n')
					#f_pov.write('\n')
					f_pov.write('camera{location <cam_x,cam_y,cam_z>\n')
					f_pov.write('look_at <0,0,0>angle 18}\n')
					f_pov.write('object{obj scale scale_f\n')
					f_pov.write('translate<0,-min_extent(obj).y*scale_f,0>\n')
					f_pov.write('translate<0,-y_size/2*scale_f,0>}\n')
					#f_pov.write('\n')
					f_pov.write('#include "povpos.pov"\n')

					f_pov.close()

			def on_each_rootdir_post(self, rootdir):
				rootdir_basename = os.path.basename(rootdir)

				# generate the output filename and open it for writing (append)
				f_inc_filepath = os.path.join(env.OUTDIR_INC, "e3d_"+rootdir_basename)+".inc"
				f_inc = open(f_inc_filepath, 'a')

				# include global .pos file
				f_global_inc_pos = open(os.path.join(env.SRCDIR_DATA, "pos.pos"), 'r')
				f_inc.write(f_global_inc_pos.read())
				f_global_inc_pos.close()

				# include local .pos file
				f_local_inc_pos = open(os.path.join(env.SRCDIR_INC, rootdir_basename, "pos.pos"), 'r')
				f_inc.write(f_local_inc_pos.read())
				f_local_inc_pos.close()

				f_inc.close()


		it = iterate_dir1(quiet)
		it.start(env.SRCDIR_INC)

		f_3dpack_add = open(os.path.join(env.SRCDIR_DATA, "3dpack_add.dat"), 'r')
		f_3dpack.write(f_3dpack_add.read())
		f_3dpack_add.close()

		f_3dpack.close()

		shutil.copy2(os.path.join(env.SRCDIR_DATA, 'prepov.pre'), os.path.join(env.OUTDIR_POV, 'povpre.pov'))
		shutil.copy2(os.path.join(env.SRCDIR_DATA, 'pospov.pos'), os.path.join(env.OUTDIR_POV, 'povpos.pov'))

		logger.info('creating release directories...')
		os.makedirs(env.RELEASEDIR)
		os.makedirs(env.RELEASEDIR_DOC)
		os.makedirs(env.RELEASEDIR_EXAMPLES)
		os.makedirs(env.RELEASEDIR_POVRAY)
		os.makedirs(env.RELEASEDIR_ULP)

		shutil.copy2(os.path.join(upDir(env.SRCDIR_ROOT), 'COPYING'), env.RELEASEDIR)

		logger.info('copying doc files to release directory...')
		for filepath in glob.glob(os.path.join(env.SRCDIR_DOC, '*')):
			shutil.copy2(filepath, env.RELEASEDIR_DOC)

		logger.info('copying example files to release directory...')
		for filepath in glob.glob(os.path.join(env.SRCDIR_EXAMPLES, '*')):
			shutil.copy2(filepath, env.RELEASEDIR_EXAMPLES)

		logger.info('copying povray files to release directory...')
		for filepath in glob.glob(os.path.join(env.OUTDIR_INC, '*.inc')):
			shutil.copy2(filepath, env.RELEASEDIR_POVRAY)
		touch(os.path.join(env.RELEASEDIR_POVRAY, "e3d_user.inc"))

		logger.info('copying data files to release directory...')
		for filepath in glob.glob(os.path.join(env.SRCDIR_DATA, 'fonts', '*.ttf')):
			shutil.copy2(filepath, env.RELEASEDIR_POVRAY)
		for filepath in glob.glob(os.path.join(env.SRCDIR_DATA, 'tex', '*.png')):
			shutil.copy2(filepath, env.RELEASEDIR_POVRAY)
		for filepath in glob.glob(os.path.join(env.SRCDIR_DATA, '*.inc')):
			shutil.copy2(filepath, env.RELEASEDIR_POVRAY)


		logger.info('copying ulp files to release directory...')

		f_3d_ulp = open(os.path.join(env.SRCDIR_ULP, "3d.ulp"), 'r')
		f_3d_ulp_content = f_3d_ulp.read().split("\n")
		f_3d_ulp.close()

		f_3dfunc_ulp = open(os.path.join(env.SRCDIR_ULP, "3dfunc.ulp"), 'r')
		f_3dfunc_ulp_content = f_3dfunc_ulp.read().split("\n")
		f_3dfunc_ulp.close()

		logger.info('writing Eagle3D 5.0 ulp files...')
		exp1 = re.compile("^#40|^#O")
		exp2 = re.compile("^#41|^#50")

		f_3d50_ulp = open(os.path.join(env.RELEASEDIR_ULP, "3d50.ulp"), 'w')
		for line in f_3d_ulp_content:
			if exp1.match(line) == None:
				if exp2.match(line) == None:
					f_3d50_ulp.write(line+"\n")
				else:
					f_3d50_ulp.write("   "+line[3:]+"\n")
		f_3d50_ulp.close()

		f_3dfunc50_ulp = open(os.path.join(env.RELEASEDIR_ULP, "3dfunc50.ulp"), 'w')
		for line in f_3dfunc_ulp_content:
			if exp1.match(line) == None:
				if exp2.match(line) == None:
					f_3dfunc50_ulp.write(line+"\n")
				else:
					f_3dfunc50_ulp.write("   "+line[3:]+"\n")
		f_3dfunc50_ulp.close()


		logger.info('writing Eagle3D 4.1 ulp files...')
		exp1 = re.compile("^#40|^#O|^#50")
		exp2 = re.compile("^#41")

		f_3d41_ulp = open(os.path.join(env.RELEASEDIR_ULP, "3d41.ulp"), 'w')
		for line in f_3d_ulp_content:
			if exp1.match(line) == None:
				if exp2.match(line) == None:
					f_3d41_ulp.write(line+"\n")
				else:
					f_3d41_ulp.write("   "+line[3:]+"\n")
		f_3d41_ulp.close()

		f_3dfunc41_ulp = open(os.path.join(env.RELEASEDIR_ULP, "3dfunc41.ulp"), 'w')
		for line in f_3dfunc_ulp_content:
			if exp1.match(line) == None:
				if exp2.match(line) == None:
					f_3dfunc41_ulp.write(line+"\n")
				else:
					f_3dfunc41_ulp.write("   "+line[3:]+"\n")
		f_3dfunc41_ulp.close()


		logger.info('writing Eagle3D 4.0 ulp files...')
		exp1 = re.compile("^#41|^#O|^#50")
		exp2 = re.compile("^#40")

		f_3d40_ulp = open(os.path.join(env.RELEASEDIR_ULP, "3d40.ulp"), 'w')
		for line in f_3d_ulp_content:
			if exp1.match(line) == None:
				if exp2.match(line) == None:
					f_3d40_ulp.write(line+"\n")
				else:
					f_3d40_ulp.write("   "+line[3:]+"\n")
		f_3d40_ulp.close()

		f_3dfunc40_ulp = open(os.path.join(env.RELEASEDIR_ULP, "3dfunc40.ulp"), 'w')
		for line in f_3dfunc_ulp_content:
			if exp1.match(line) == None:
				if exp2.match(line) == None:
					f_3dfunc40_ulp.write(line+"\n")
				else:
					f_3dfunc40_ulp.write("   "+line[3:]+"\n")
		f_3dfunc40_ulp.close()



		shutil.copy2(os.path.join(env.SRCDIR_ULP, 'eagle2svg.ulp'), env.RELEASEDIR_ULP)
		for filepath in glob.glob(os.path.join(env.SRCDIR_ULP, '3dlang*.dat')):
			shutil.copy2(filepath, env.RELEASEDIR_ULP)
		for filepath in glob.glob(os.path.join(env.SRCDIR_ULP, '3dcol*.dat')):
			shutil.copy2(filepath, env.RELEASEDIR_ULP)
		for filepath in glob.glob(os.path.join(env.SRCDIR_ULP, '3d*.png')):
			shutil.copy2(filepath, env.RELEASEDIR_ULP)
		touch(os.path.join(env.RELEASEDIR_ULP, "3dconf.dat"))

		shutil.copy2(os.path.join(env.OUTDIR_3DPACK, '3dpack.dat'), env.RELEASEDIR_ULP)

		logger.info('done')
		logger.info('totalerrors: %s'%(str(total_errors)))


	########################################
	#
	def release(self):
		quiet = config._get('quiet')
		version = config._get('version')

		total_errors = 0

		logger.info('setting the current version in all files...')
		for filepattern in ['*.ulp', '*.dat', '*.inc', '*.txt']:
			for rootdir, dirlist, filelist in os.walk(env.RELEASEDIR):
				for filepath in glob.glob(os.path.join(rootdir, filepattern)):
					if not quiet: logger.info('  %s'%(filepath))
					#retcode = subprocess.call(["sed", "-i", "s,###VERSIONDUMMY###,%s,"%(version), filepath])
					retcode = subprocess_call(["sed", "-i", "s,###VERSIONDUMMY###,%s,"%(version), filepath])
					if retcode != 0:
						total_errors = total_errors+1

		logger.info('preparing release for *nix systems...')
		_dos2unix = config._getbin('dos2unix')
		if _dos2unix:
			logger.info('making UNIX line endings for all text files...')
			for filepattern in ['*.sh', '*.pl', '*.inc', '*.src', '*.dat', '*.pos', '*.pre', '*.inc', '*.ulp', '*.pov', '*.ini', '*.txt']:
				for rootdir, dirlist, filelist in os.walk(env.RELEASEDIR):
					for filepath in glob.glob(os.path.join(rootdir, filepattern)):
						if not quiet: logger.info('  %s'%(filepath))
						retcode = subprocess_call([_dos2unix, filepath])
						if retcode != 0:
							total_errors = total_errors+1

		_tar = config._getbin('tar')
		if _tar:
			if config._getbin('bzip2'):
				filepath = os.path.join(env.ARCHIVE_OUTPUT_DIR, "eagle3d_"+self.version_to_filename()+".tar.bz2")
				command = [_tar, '-c', '-a', '-f', filepath, os.path.basename(env.RELEASEDIR) ]
				logger.info('calling: '+" ".join(command))
				retcode = subprocess_call(command, env.OUTDIR_ROOT)
				if retcode != 0:
					total_errors = total_errors+1
			else:
				logger.info('cound not find bzip2, not making tar.bz2 archive')

			if config._getbin('gzip'):
				filepath = os.path.join(env.ARCHIVE_OUTPUT_DIR, "eagle3d_"+self.version_to_filename(version)+".tar.gz")
				command = [_tar, '-c', '-a', '-f', filepath, os.path.basename(env.RELEASEDIR) ]
				logger.info('calling: '+" ".join(command))
				retcode = subprocess_call(command, env.OUTDIR_ROOT)
				if retcode != 0:
					total_errors = total_errors+1
			else:
				logger.info('cound not find bzip2, not making tar.bz2 archive')
		else:
			logger.info('cound not find tar, not making tar.* archives')

		logger.info('preparing release for non *nix systems...')
		_bin = False
		_bin = config._getbin('todos')
		if not _bin:
			_bin = config._getbin('unix2dos')
		if _bin:
			logger.info('making DOS line endings for all text files...')
			for filepattern in ['*.sh', '*.pl', '*.inc', '*.src', '*.dat', '*.pos', '*.pre', '*.inc', '*.ulp', '*.pov', '*.ini', '*.txt']:
				for rootdir, dirlist, filelist in os.walk(env.RELEASEDIR):
					for filepath in glob.glob(os.path.join(rootdir, filepattern)):
						if not quiet: logger.info('  %s'%(filepath))
						subprocess_call([_bin, filepath])
						if retcode != 0:
							total_errors = total_errors+1

		_zip = config._getbin('zip')
		if _zip:
			filepath = os.path.join(env.ARCHIVE_OUTPUT_DIR, "eagle3d_"+version_to_filename(version)+".zip")
			command = [_zip, '-9', '-q', '-r', filepath, os.path.basename(env.RELEASEDIR) ]
			logger.info('calling: '+" ".join(command))
			retcode = subprocess_call(command, env.OUTDIR_ROOT)
			if retcode != 0:
				total_errors = total_errors+1

		logger.info('done')
		logger.info('totalerrors: %s'%(str(total_errors)))


	########################################
	#
	def render(self):
		quiet = config._get('quiet')
		render_dryrun = config._get('render_dryrun')

		render_bin = config._getbin('povray')
		if not render_bin and not render_dryrun:
				logger.info("could not find rendering executable, exiting.")
				return -1

		total_errors = 0

		nice_bin = config._getbin('nice')
		if nice_bin:
			nice_bin = nice_bin+" -n 19"

		logger.info('creating output directories...')
		if not os.path.exists(env.OUTDIR_IMG):
			os.makedirs(env.OUTDIR_IMG)

		if os.path.exists(os.path.join(env.OUTDIR_IMG, "warning")):
			if os.path.isdir(os.path.join(env.OUTDIR_IMG, "warning")):
				shutil.rmtree(os.path.join(env.OUTDIR_IMG, "warning"))
		os.makedirs(os.path.join(env.OUTDIR_IMG, "warning"))
		if os.path.exists(os.path.join(env.OUTDIR_IMG, "fatal")):
			if os.path.isdir(os.path.join(env.OUTDIR_IMG, "fatal")):
				shutil.rmtree(os.path.join(env.OUTDIR_IMG, "fatal"))
		os.makedirs(os.path.join(env.OUTDIR_IMG, "fatal"))

		render_povdir = env.OUTDIR_POV
		render_incdir = env.RELEASEDIR_POVRAY
		render_outdir = env.OUTDIR_IMG

		render_mask = config._get('render_mask')
		render_noclobber = config._get('render_noclobber')
		render_procs = config._get('render_procs')
		img_extension = config._get('img_extension')

		template_values = {}
		template_values['nice_bin'] = nice_bin
		template_values['render_bin'] = render_bin
		template_values['render_incdir'] = render_incdir
		template_values['render_povdir'] = render_povdir
		template_values['render_size_x'] = str(config._get('render_size_x'))
		template_values['render_size_y'] = str(config._get('render_size_y'))
		template_values['render_aa'] = str(config._get('render_aa'))
		template_values['render_outdir'] = render_outdir
		template_values['img_extension'] = img_extension

		command_template = string.Template("""${nice_bin} ${render_bin} +L${render_incdir}
                                                                        +L${render_povdir}
                                                                        +W${render_size_x} +H${render_size_y} +A${render_aa}
                                                                        -GW${render_outdir}/warning/${render_file_basename}.warnings.log
                                                                        -GF${render_outdir}/fatal/${render_file_basename}.fatal.log
                                                                        +O${render_outdir}/${render_file_basename}${img_extension}
                                                                        -GS -GR -GD -V -D +I${render_file_fullname}""")

		if not render_dryrun:
			pq = ProcessQueue(max_proc=render_procs, logger=logger)
			pq.start()

		total_rendering_attempts = 0
		total_rendering_skipped = 0

		logger.info("rendering parts...")
		for rootdir, dirlist, filelist in os.walk(render_povdir):
			filelist.sort()
			for f in filelist:
				if fnmatch.fnmatch(f, render_mask):
					if f in ["povpos.pov", "povpre.pov"]:
						continue
					target_render_filepath = os.path.join(render_outdir, f+img_extension)
					if render_noclobber and os.path.exists(target_render_filepath):
						logger.info("skipping %s, image exists."%(f+img_extension))
						total_rendering_skipped = total_rendering_skipped+1
						continue

					if not quiet: logger.info("rendering "+f)

					template_values['render_file_basename'] = f
					template_values['render_file_fullname'] = os.path.join(rootdir, f)

					command = command_template.substitute(template_values)
					command = " ".join(command.split())
					if not render_dryrun:
						pq.add_process(command, f)
					else:
						if not quiet: logger.info("\ncommand: %s"%(command))
						touch(target_render_filepath)

					total_rendering_attempts = total_rendering_attempts+1

		if not render_dryrun:
			pq.wait()
			del pq

		logger.info("a total of %d rendering processes were attempted."%(total_rendering_attempts))
		if render_noclobber:
			logger.info("a total of %d rendering processes were skipped."%(total_rendering_skipped))

		# removing empty fatal files
		fatal_rendering_procs = 0
		for rootdir, dirlist, filelist in os.walk(os.path.join(render_outdir, "fatal")):
			filelist.sort()
			for f in filelist:
				if fnmatch.fnmatch(f, "*.log"):
					f_fullpath = os.path.join(rootdir, f)
					if os.path.getsize(f_fullpath) == 0:
						os.remove(f_fullpath)
					else:
						fatal_rendering_procs = fatal_rendering_procs+1
		if fatal_rendering_procs > 0:
			logger.info("%d of %d rendering processes had fatal errors."%(fatal_rendering_procs, total_rendering_attempts))
			logger.info("check %s."%(os.path.join(render_outdir, "fatal")))

		total_errors = total_errors + fatal_rendering_procs

		# removing empty warning files
		warning_rendering_procs = 0
		for rootdir, dirlist, filelist in os.walk(os.path.join(render_outdir, "warning")):
			filelist.sort()
			for f in filelist:
				if fnmatch.fnmatch(f, "*.log"):
					f_fullpath = os.path.join(rootdir, f)
					if os.path.getsize(f_fullpath) == 0:
						os.remove(f_fullpath)
					else:
						warning_rendering_procs = warning_rendering_procs+1
		if warning_rendering_procs > 0:
			logger.info("%d of %d rendering processes had warnings."%(warning_rendering_procs, total_rendering_attempts))
			logger.info("check %s."%(os.path.join(render_outdir, "warnings")))

		total_errors = total_errors + warning_rendering_procs

		total_rendering_results = total_rendering_attempts + total_rendering_skipped
		_im_montage = config._getbin('montage')
		render_colsperpage = config._get('render_colsperpage')
		render_rowsperpage = config._get('render_rowsperpage')
		if _im_montage:
			logger.info("rendering part gallery file(s)...")
			if not render_dryrun:
				pq = ProcessQueue(max_proc=1, logger=logger)
				pq.start()

			tile_geometry = "%dx%d"%(render_colsperpage, render_rowsperpage)
			montage_command_base = ["cd", render_outdir, "&&", nice_bin, _im_montage, "-geometry", "128x96", "-tile", tile_geometry]

			items_per_gallery_page = render_colsperpage*render_rowsperpage

			gallery_pages = [[]]
			gallery_page_item_count = 0

			for rootdir, dirlist, filelist in os.walk(os.path.join(render_outdir)):
				filelist.sort()
				for f in filelist:
					#if fnmatch.fnmatch(f, "*.png"):
					#if fnmatch.fnmatch(f, render_mask.replace(".pov", "*"+img_extension)):
					if fnmatch.fnmatch(f, render_mask+"*"+img_extension):
						if gallery_page_item_count == items_per_gallery_page:
							gallery_page_item_count = 0
							gallery_pages.append([])
						#gallery_pages[-1].append(os.path.join(rootdir, f))
						gallery_pages[-1].append(f)
						gallery_page_item_count = gallery_page_item_count+1

			for i in range(0, len(gallery_pages)):
				montage_title = "gallery, page %d"%(i)
				montage_command = montage_command_base + gallery_pages[i]
				#if total_rendering_results <= items_per_gallery_page:
					#montage_command.append(os.path.join(upDir(render_outdir), "gallery"+img_extension))
				#else:
					#montage_command.append(os.path.join(upDir(render_outdir), "gallery-%d%s"%(i, img_extension)))
				montage_command.append(os.path.join(upDir(render_outdir), "gallery-%d%s"%(i, img_extension)))
				command = " ".join(montage_command)
				if not render_dryrun:
					pq.add_process(command, montage_title)
				else:
					logger.info("rendering %s"%(montage_title))
					if not quiet: logger.info("\ncommand: %s"%(command))

			if not render_dryrun:
				pq.wait()
				del pq

		return total_errors


	########################################
	#
	def renderhtml(self):
		quiet = config._get('quiet')
		render_dryrun = config._get('render_dryrun')

		src_inc_prefix_map = config._get('src_inc_prefix_map')

		render_outdir = env.OUTDIR_IMG
		povray_outdir = env.OUTDIR_POV
		render_htmldir = os.path.join(env.OUTDIR_IMG, "html")
		render_thumbnaildir = os.path.join(env.OUTDIR_IMG, "html", "thumbnail")
		convert_bin = config._getbin('convert')

		renderhtml_thumb_size_x = config._get('renderhtml_thumb_size_x')
		renderhtml_thumb_size_y = config._get('renderhtml_thumb_size_y')

		img_extension = config._get('img_extension')
		render_extension = config._get('render_extension')

		logger.info('cleaning/creating output directories...')
		if not os.path.exists(env.OUTDIR_IMG):
			os.makedirs(env.OUTDIR_IMG)
		if not render_dryrun:
			if os.path.exists(render_htmldir):
				if os.path.isdir(render_htmldir):
					shutil.rmtree(render_htmldir)
			os.makedirs(render_htmldir)
			os.makedirs(render_thumbnaildir)
		else:
			if not os.path.exists(render_htmldir):
				os.makedirs(render_htmldir)
			if not os.path.exists(render_thumbnaildir):
				os.makedirs(render_thumbnaildir)

		if not render_dryrun:
			pq = ProcessQueue(max_proc=16, logger=logger)
			pq.start()
			command = string.Template("""${convert_bin} -geometry ${thumbnail_size_x}x${thumbnail_size_x} ${thumbnail_input_filepath} ${thumbnail_output_filepath}""")

		template_values = {}
		template_values['eagle3d_string'] = "Eagle3D"
		template_values['convert_bin'] = convert_bin
		template_values['thumbnail_size_x'] = renderhtml_thumb_size_x
		template_values['thumbnail_size_y'] = renderhtml_thumb_size_y

		renderhtml_cols = config._get('renderhtml_cols')
		renderhtml_rows = config._get('renderhtml_rows')
		template_values['cell_size_x'] = str(100/renderhtml_cols)+"%"
		template_values['cell_size_y'] = ""

		htmlFileWriter = HtmlFileWriter(renderhtml_cols, renderhtml_rows, quiet)

		html_header_template = string.Template("""<html>
<head>
	<style type="text/css">
		table.parts {
			border-width: 1px;
			border-spacing: 3px;
			border-style: outset;
			border-color: black;
			background-color: white;
		}
		table.parts th {
			border-width: 1px;
			padding: 3px;
			border-style: outset;
			border-color: black;
			background-color: white;
		}
		table.parts td {
			border-width: 1px;
			padding: 3px;
			border-style: outset;
			border-color: black;
			background-color: white;
			text-align: center;
		}
	</style>
	<title>${eagle3d_string} - %TITLE%</title>
</head>
<body>
<center><h3>${eagle3d_string} - %TITLE%</h3></center>
<center>
	<table class="parts">
		<tr>
""")
		html_body_template = string.Template("""			<td width="${cell_size_x}" height="${cell_size_y}" title="${thumbnail_text}" >
				<a href="${thumbnail_a_href}" >
					<img width="${thumbnail_size_x}" height="${thumbnail_size_y}" src="${thumbnail_img_src}" />
				</a>
			</td>
""")
		html_footer_template = string.Template("""	</tr>
	</table>
</center>
<center>
	<p>%PAGE_LINKS%</p>
	<p></p>
	<p></p>
	<p>${eagle3d_string}</p>
</center>
</body>
</html>
""")

		src_inc_prefix_map_swap = dict((value, key) for key, value in src_inc_prefix_map.iteritems())

		htmlFileWriter.set_header_string(html_header_template.substitute(template_values))
		htmlFileWriter.set_footer_string(html_footer_template.substitute(template_values))

		#by using the povray directory, we will be generating a list of the images that _should_ exist
		for rootdir, dirlist, filelist in os.walk(povray_outdir):
			filelist.sort()
			for f in filelist:
				if fnmatch.fnmatch(f, "*"+render_extension):
					f_prefix = f.split('_')[0]+'_'
					if f_prefix in src_inc_prefix_map_swap:
						subdir = src_inc_prefix_map_swap[f_prefix]
					else:
						subdir = "unknown"

					#generate the image name the same way it is during rendering
					img_basename = f+img_extension
					img_barename = f[:-len(render_extension)]
					template_values['thumbnail_input_filepath'] = os.path.join(render_outdir, img_basename)
					template_values['thumbnail_output_filepath'] = os.path.join(render_thumbnaildir, img_basename)
					template_values['thumbnail_a_href'] = os.path.join("..", img_basename)
					template_values['thumbnail_img_src'] = os.path.join("thumbnail", img_basename)
					template_values['thumbnail_text'] = img_barename

					filepath = os.path.join(render_htmldir, "index."+subdir+"-%d.html")
					title = subdir
					htmlFileWriter.write_header(filepath, title)

					if not render_dryrun:
						cmd = command.substitute(template_values)
						pq.add_process(" ".join(cmd.split()), f)

					htmlFileWriter.write_body(filepath, html_body_template.substitute(template_values))

		if not render_dryrun:
			pq.wait()
			del pq

		htmlFileWriter.write_all_footers()


config = _ConfigParser()
worker = _Worker()
###############################################################################
# entry
# this constuct allows the file to be imported as a module as well as executed.
if __name__ == "__main__":

	usage_string = """Usage: %s [ACTION] [options]
  only two options may be used without an action:
    --rewrite-config:
    --recheck-config
  ACTION       The administrative action to be performed.
  help         show help for all commands.
  clean        remove previous attempts to create an eagle3d distribution.
  create       create an eagle3d distribution.
  verify       verify that include files are the correct format.
  release      set VERSION variable in files and create archives.
  render       render example images for Eagle3D parts.
  renderhtml   generate an HTML digest of parts from rendered example images.
  env          dump env settings."""%(os.path.basename(sys.argv[0]))

	parser = OptionParser(usage=usage_string,
	                      version="%prog v"+SCRIPT_VERSION,
	                      description="%prog is a administrative utility used to generate, test and release Eagle3D.",
	                      add_help_option=False)
	parser.add_option("-h", "--help",
	                  action="store_true", dest="help", default=False,
	                  help="show this help message and exit.")
	parser.add_option("--noconsole",
	                  action="store_true", dest="noconsole", default=False,
	                  help="do not print to console, only to log file (default is %default).")
	parser.add_option("-q", "--quiet",
	                  action="store_true", dest="quiet", default=False,
	                  help="do not print 'no errors' message (default is %default).")
	parser.add_option("-s", "--silent",
	                  action="store_true", dest="silent", default=False,
	                  help="print and log nothing, return non-zero on any error (default is %default).")
	parser.add_option("--rewrite-config",
	                  action="store_true", dest="rewrite_config", default=False,
	                  help="overwrite the existing configuration file, replacing it with defaults and rechecking paths")
	parser.add_option("--recheck-config",
	                  action="store_true", dest="recheck_config", default=False,
	                  help="overwrite the existing system section of the configuration file, rechecking the paths")
	parser.add_option("-d", "--debug",
	                  action="count", dest="debugmode", default=0,
	                  help="""run in debugging mode.
this option may be used multiple times.
one will count the number of times each line is executed.
two will output a trace of each line as it is being counted.
three will output a list of functions that were called at least once.
this option default is %default""")

	option_groups = []

	option_groups.append(OptionGroup(parser, "create", None))
	option_groups[-1].add_option("--create-mask",
	                             action="store", dest="create_mask", default="*.inc.src", metavar="[STRING]",
	                             help="name mask of files to process (default is %default).")
	parser.add_option_group(option_groups[-1])

	option_groups.append(OptionGroup(parser, "verify", None))
	option_groups[-1].add_option("--verify-mask",
	                             action="store", dest="verify_mask", default="*.inc.src", metavar="[STRING]",
	                             help="name mask of files to process (default is %default).")
	option_groups[-1].add_option("--full-check",
	                             action="store_true", dest="verify_full_check", default=False,
	                             help="when verifying, also check sub-macros (default is %default).")
	parser.add_option_group(option_groups[-1])

	option_groups.append(OptionGroup(parser, "release", None))
	option_groups[-1].add_option("--name",
	                             action="store", dest="release_name", metavar="[STRING]",
	                             help="the name used when creating release archives.")
	parser.add_option_group(option_groups[-1])

	option_groups.append(OptionGroup(parser, "render", None))
	option_groups[-1].add_option("-x", "--size-x",
	                             action="store", dest="render_size_x", default="640", metavar="[INT]", type="int",
	                             help="x size (width) of rendered images (default is %default).")
	option_groups[-1].add_option("-y", "--size-y",
	                             action="store", dest="render_size_y", default="480", metavar="[INT]", type="int",
	                             help="y size (height) of rendered images (default is %default).")
	option_groups[-1].add_option("--anti-alias",
	                             action="store", dest="render_aa", default="0.3", metavar="[FLOAT]", type="float",
	                             help="anti-alias value of rendered images (default is %default).")
	option_groups[-1].add_option("--processes",
	                             action="store", dest="render_procs", default="16", metavar="[INT]", type="int",
	                             help="number of rendering processes to spawn (default is %default).")
	option_groups[-1].add_option("--render-mask",
	                             action="store", dest="render_mask", default="*.pov", metavar="[STRING]",
	                             help="name mask of files to process (default is %default).")
	option_groups[-1].add_option("--noclobber",
	                             action="store_true", dest="render_noclobber", default=False,
	                             help="do not render files that exist (default is %default).")
	option_groups[-1].add_option("--dry-run",
	                             action="store_true", dest="render_dryrun", default=False,
	                             help="do not render files, only print command that would have been used (default is %default).")
	option_groups[-1].add_option("--cols-per-page",
	                             action="store", dest="render_colsperpage", default="15", metavar="[INT]", type="int",
	                             help="number of images to render per column on each page of the gallery (default is %default).")
	option_groups[-1].add_option("--rows-per-page",
	                             action="store", dest="render_rowsperpage", default="10", metavar="[INT]", type="int",
	                             help="number of images to render per row on each page of the gallery (default is %default).")
	parser.add_option_group(option_groups[-1])

	option_groups.append(OptionGroup(parser, "renderhtml", None))
	option_groups[-1].add_option("--thumb-size-x",
	                             action="store", dest="renderhtml_thumb_size_x", default="64", metavar="[INT]", type="int",
	                             help="x size (width) of thumbnail images")
	option_groups[-1].add_option("--thumb-size-y",
	                             action="store", dest="renderhtml_thumb_size_y", default="48", metavar="[INT]", type="int",
	                             help="y size (height) of thumbnail images")
	option_groups[-1].add_option("--cols",
	                             action="store", dest="renderhtml_cols", default="15", metavar="[INT]", type="int",
	                             help="number of thumbnail image columns per row")
	option_groups[-1].add_option("--rows",
	                             action="store", dest="renderhtml_rows", default="10", metavar="[INT]", type="int",
	                             help="number of images to render per row on each page of the gallery (default is %default)")
	parser.add_option_group(option_groups[-1])


	#parse the command line arguments
	(options, args) = parser.parse_args()
	#load the config
	config.read_config()
	if not config.config_exists():
		config.update_options(options)
		config.write_config()

	#if the command line arg is set, reset the system section of config
	if options.recheck_config:
		config.set_system_config()
		config.write_config()

	#apply any command line arguments to the options
	config.update_options(options, parser.get_default_values())

	if options.rewrite_config:
		config.write_config()

	# check for an action
	action = None
	if len(sys.argv) > 1 and sys.argv[1] in ["help", "clean", "create", "verify", "release", "render", "renderhtml", "env"]:
		action = sys.argv[1]
	else:
		if options.rewrite_config or options.recheck_config:
			sys.exit(0)
		else:
			parser.print_help()
			sys.exit(1)

	worker.timestamp = datetime.datetime.now()
	logger = logging.getLogger(action)

	if options.help or action == 'help':
		parser.print_help()
		sys.exit(1)

	env.init()
	if env.WORKDIR == None:
		parser.print_help()
		sys.exit(1)

	if not options.silent:
		loghandler = logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), action+'.log'), 'w')
		loghandler.setFormatter(logging.Formatter('%(message)s'))
		logger.addHandler(loghandler)
		logger.setLevel(logging.INFO)

	if not options.silent and not options.noconsole:
		logger.addHandler(logging.StreamHandler())

	if options.debugmode:
		import sys, trace
		#_outfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eagle3d-debug-"+action+".log")
		_outfile = "eagle3d-debug-"+action+".log"
		if options.debugmode > 0:
			_trace=0; _count=1; _countfuncs=0; _countcallers=0
		if options.debugmode > 1:
			_trace=1; _count=1; _countfuncs=0; _countcallers=0
		if options.debugmode > 2:
			_trace=0; _count=0; _countfuncs=1; _countcallers=1
		tracer = trace.Trace(ignoredirs=[sys.prefix, sys.exec_prefix,], trace=_trace, count=_count, countfuncs=_countfuncs, countcallers=_countcallers, outfile=_outfile)
		if action == "verify":
			tracer.run('worker.verify()')
		elif action == "clean":
			tracer.run('worker.clean()')
		elif action == "create":
			tracer.run('worker.create()')
		elif action == "release":
			tracer.run('worker.release()')
		elif action == "render":
			tracer.run('worker.render()')
		elif action == "renderhtml":
			tracer.run('worker.renderhtml()')
		elif action == "env":
			tracer.run('env.dump()')
		r = tracer.results()
		r.write_results(show_missing=True, coverdir=os.path.dirname(os.path.abspath(__file__)))
	else:
		if action == "verify":
			sys.exit(worker.verify())
		elif action == "clean":
			sys.exit(worker.clean())
		elif action == "create":
			sys.exit(worker.create())
		elif action == "release":
			sys.exit(worker.release())
		elif action == "render":
			sys.exit(worker.render())
		elif action == "renderhtml":
			sys.exit(worker.renderhtml())
		elif action == "env":
			sys.exit(worker.dump())
