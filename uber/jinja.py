import os
import sys
from functools import lru_cache
from types import FunctionType

import jinja2
from jinja2.environment import Template
from jinja2.loaders import split_template_path
from jinja2.utils import open_if_exists
from jinja2.exceptions import TemplateNotFound, TemplatesNotFound
from sideboard.lib import request_cached_property

from uber.config import c

# This used to be in jinja2._compat, but that was removed in version 3.0.0
string_types = (str,)
if sys.version_info[0] == 2:
    string_types = (str,unicode)


class MultiPathEnvironment(jinja2.Environment):

    @request_cached_property
    def _templates_loaded_for_current_request(self):
        return set()

    @lru_cache()
    def _get_matching_filenames(self, template):
        pieces = split_template_path(template)
        matching_filenames = []
        for searchpath in self.loader.searchpath:
            filename = os.path.join(searchpath, *pieces)
            if os.path.exists(filename):
                matching_filenames.append(filename)
        return matching_filenames

    def _load_template(self, name, globals, use_request_cache=True):
        """
        Overridden to consider templates already loaded by the current request.
        """
        if not use_request_cache:
            return super(MultiPathEnvironment, self)._load_template(name, globals)

        if self.loader is None:
            raise TypeError('no loader for this environment specified')

        matching_files = self._get_matching_filenames(name)
        if not matching_files:
            raise TemplateNotFound(name)

        loaded_templates = self._templates_loaded_for_current_request
        unused_files = [s for s in matching_files if s not in loaded_templates]
        filename = unused_files[0] if unused_files else matching_files[-1]

        cache_key = filename
        if self.cache is not None:
            template = self.cache.get(cache_key)
            if template and (not self.auto_reload or template.is_up_to_date):
                self._templates_loaded_for_current_request.add(template.filename)
                return template

        template = self.loader.load(self, filename, globals)
        self._templates_loaded_for_current_request.add(template.filename)

        if self.cache is not None:
            self.cache[cache_key] = template
        return template

    def get_template(self, name, parent=None, globals=None, use_request_cache=True):
        """
        Overridden to add the `use_request_cache` parameter.
        """
        if isinstance(name, Template):
            return name
        if parent is not None:
            name = self.join_path(name, parent)
        return self._load_template(name, self.make_globals(globals), use_request_cache)

    def select_template(self, names, parent=None, globals=None, use_request_cache=True):
        """
        Overridden to add the `use_request_cache` parameter.
        """
        if not names:
            raise TemplatesNotFound(message='Tried to select from an empty list of templates.')

        globals = self.make_globals(globals)
        for name in names:
            if isinstance(name, Template):
                return name
            if parent is not None:
                name = self.join_path(name, parent)
            try:
                return self._load_template(name, globals, use_request_cache)
            except TemplateNotFound:
                pass
        raise TemplatesNotFound(names)

    def get_or_select_template(self, template_name_or_list, parent=None, globals=None, use_request_cache=True):
        """
        Overridden to add the `use_request_cache` parameter.
        """
        if isinstance(template_name_or_list, string_types):
            return self.get_template(template_name_or_list, parent, globals, use_request_cache)
        elif isinstance(template_name_or_list, Template):
            return template_name_or_list
        return self.select_template(template_name_or_list, parent, globals, use_request_cache)


class AbsolutePathLoader(jinja2.FileSystemLoader):

    def get_source(self, environment, template):
        """
        Overridden to also accept absolute paths.
        """
        if not os.path.isabs(template):
            return super(AbsolutePathLoader, self).get_source(environment, template)

        # Security check, ensure the abs path is part of a valid search path
        if not any(template.startswith(s) for s in self.searchpath):
            raise TemplateNotFound(template)

        f = open_if_exists(template)
        if f is None:
            raise TemplateNotFound(template)
        try:
            contents = f.read().decode(self.encoding)
        finally:
            f.close()

        mtime = os.path.getmtime(template)

        def uptodate():
            try:
                return os.path.getmtime(template) == mtime
            except OSError:
                return False
        return contents, template, uptodate


class JinjaEnv:
    _env = None
    _exportable_functions = {}
    _filter_functions = {}
    _test_functions = {}
    _template_dirs = []

    @classmethod
    def insert_template_dir(cls, dirname):
        """
        Add another template directory we should search when looking up templates.
        """
        if cls._env and cls._env.loader:
            cls._env.loader.searchpath.insert(0, dirname)
        else:
            cls._template_dirs.insert(0, dirname)

    @classmethod
    def env(cls):
        if cls._env is None:
            cls._env = cls._init_env()
        return cls._env

    @classmethod
    def _init_env(cls):
        env = MultiPathEnvironment(
            autoescape=True,
            loader=AbsolutePathLoader(cls._template_dirs),
            lstrip_blocks=True,
            trim_blocks=True,
        )

        for name, func in cls._exportable_functions.items():
            env.globals[name] = func

        for name, func in cls._filter_functions.items():
            env.filters[name] = func

        for name, func in cls._test_functions.items():
            env.tests[name] = func

        return env

    @classmethod
    def jinja_export(cls, name=None):
        def _register(func, _name=None):
            if cls._env:
                cls._env.globals[_name if _name else func.__name__] = func
            else:
                cls._exportable_functions[_name if _name else func.__name__] = func

        if isinstance(name, FunctionType):
            _register(name)
            return name
        else:
            def registrar(func):
                _register(func, name)
                return func
            registrar.__name__ = name
            return registrar

    @classmethod
    def jinja_filter(cls, name=None):
        def _register(func, _name=None):
            if cls._env:
                cls._env.filters[_name if _name else func.__name__] = func
            else:
                cls._filter_functions[_name if _name else func.__name__] = func

        if isinstance(name, FunctionType):
            _register(name)
            return name
        else:
            def registrar(func):
                _register(func, name)
                return func
            registrar.__name__ = name
            return registrar

    @classmethod
    def jinja_test(cls, name=None):
        def _register(func, _name=None):
            if cls._env:
                cls._env.tests[_name if _name else func.__name__] = func
            else:
                cls._test_functions[_name if _name else func.__name__] = func

        if isinstance(name, FunctionType):
            _register(name)
            return name
        else:
            def registrar(func):
                _register(func, name)
                return func
            registrar.__name__ = name
            return registrar


def template_overrides(dirname):
    """
    Each event can have its own plugin and override our default templates with
    its own by calling this method and passing its templates directory.
    """
    JinjaEnv.insert_template_dir(dirname)


for _directory in c.TEMPLATE_DIRS:
    template_overrides(_directory)
