#!/usr/bin/python3
import re
from copy import deepcopy
from pathlib import Path
from typing import Any


class Input:
    def __init__(self, in_dir: Path, out_dir: Path, files: list[str], verbose: bool):
        self.__in_dir = in_dir
        self.__out_dir = out_dir
        self.__files = files
        self.__verbose = verbose

    @property
    def verbose(self):
        return self.__verbose

    @property
    def in_dir(self):
        return self.__in_dir

    @property
    def out_dir(self):
        return self.__out_dir

    @property
    def files(self):
        return self.__files


def validate_input(arguments: dict[str, Any]) -> Input:
    """
    Validates input arguments.
    :param arguments: dict of input args.
    :return: validated args.
    """
    in_dir = Path(arguments['input_directory'][0])
    if not in_dir.exists():
        raise ValueError(f'Input directory path {in_dir} does not refer to an existing directory.')
    if not in_dir.is_dir():
        raise ValueError(f'Input directory path {in_dir} does not refer to a directory')

    files = arguments['schemas']
    json_files = [file for file in files if file.endswith('.json')]
    if len(files) != len(json_files):
        print('Some of the input schema names were not JSON names (terminating in .json). These will be skipped')

    if not json_files:
        raise ValueError(f'After filtering for json-only schema names, none left remaining from {files}. Aborting.')

    out_dir = Path(arguments['output_directory'][0])
    if not out_dir.exists():
        print(f'Output directory {out_dir} does not exist, it will be created.')
    elif not out_dir.is_dir():
        raise ValueError(f'Output directory path {out_dir} does not refer to a directory')

    return Input(in_dir, out_dir, files, arguments['verbose'])


def acquire_paths_of_schemas_relative_to_input_dir(in_args: Input) -> list[Path]:
    """
    Obtains list of schema paths found in the input directory.
    :param in_args: args containing input dir.
    :return: list of paths relative to input dir.
    """
    from os import walk
    paths = []

    for root, _, files in walk(in_args.in_dir):
        if root.startswith(str(in_args.out_dir.as_posix())):
            continue

        relative_root = Path(root).relative_to(in_args.in_dir)
        for file in files:
            paths.append(relative_root / file)

    return paths


def acquire_paths_of_schemas_to_bundle(in_args: Input, schema_paths: list[Path]) -> list[Path]:
    """
    Obtains list of schema paths found in the input directory that match the requested schemas to bundle.
    :param in_args: input arguments.
    :param schema_paths: list of paths relative to input dir containing all detected schemas.
    :return: list of paths relative to input dir of schemas to output bundled.
    """
    if in_args.verbose:
        for file in in_args.files:
            if file not in map(lambda p: p.name, schema_paths):
                print(f'Warning: no schema matched input argument {file}')

    return [p for p in schema_paths if p.name in in_args.files]


def parse_schemas(in_args: Input, schema_paths: list[Path]) -> list[tuple[Path, dict[str, Any]]]:
    """
    Reads each schema at given paths and returns a list of relative path -> dict of JSON.
    :param in_args: input args.
    :param schema_paths: paths of schema files relative to input dir.
    :return: list of relative path -> dict of JSON contents.
    """
    from json import load

    contents = []
    for s in schema_paths:
        with open(in_args.in_dir / s, mode='r') as f:
            contents.append((s, load(f)))

    return contents


def locate_content_root(in_args: Input, schema_relative_paths_and_contents: list[tuple[Path, dict[str, Any]]]) -> str:
    """
    Obtains the content root of the CDN link.
    Required later to locate where local referenced files are locally based on content root.
    Obtained by removing relative path to project root from CDN link.
    :param in_args: input args.
    :param schema_relative_paths_and_contents: list of relative path -> dict of JSON contents.
    :return: content root link.
    """
    content_root = None
    content_root_obtained_from = None

    assert schema_relative_paths_and_contents

    for path, contents in schema_relative_paths_and_contents:
        # We obtain the CDN link of this file from the '$id' root meta field.
        if '$id' not in contents:
            raise ValueError(f"Schema at '{path}' is invalid: no root '$id' field.")

        if not content_root:
            content_root = contents['$id'].removesuffix(str(path.as_posix()))
            content_root_obtained_from = path
        else:
            # But we must also validate that all '$id' root meta fields are assigned correctly (same content root).
            current_content_root = contents['$id'].removesuffix(str(path.as_posix()))
            if content_root != current_content_root:
                raise ValueError(
                    f"Schema at '{path}' defines a different content root ('{current_content_root}') than the one identified at '{content_root_obtained_from}' (which is '{content_root}')")

    if in_args.verbose:
        print(f"Identified content root as '{content_root}'")

    return content_root


def extract_references_single(in_args: Input, current_node: Any, schema_key_in_decomposed_objects: str,
                              reference_paths: list[tuple[str, str]], current_path: str = '$') -> None:
    """
    Parses JSON dict and stores the path and objects containing '$ref' fields. Ignores '$ref' fields that refer to
    current object (#). Searches recursively, starting from path '$' - current object.
    :param in_args: script input args.
    :param current_node: current schema contents.
    :param schema_key_in_decomposed_objects: key in 'decomposed_objects' of this schema.
    :param reference_paths: list to collect reference paths in.
    :param current_path: path currently at in the search.
    :return: None.
    """

    # If this node is an object '{}', we can find '$ref', and must construct an object-like path (a.b)
    if isinstance(current_node, dict):
        for k, v in current_node.items():
            if k == '$ref':
                if v.startswith('#') and len(v) > 1:
                    if in_args.verbose:
                        print(f"---- Skipping reference to subschema in current object '{schema_key_in_decomposed_objects}' at '{current_path}'")
                    continue

                reference_paths.append((current_path, schema_key_in_decomposed_objects))
                if in_args.verbose:
                    print(f"---- Identified reference in decomposed object '{schema_key_in_decomposed_objects}' at '{current_path}'")

                # Previously, we had 'continue' here, given that we could assume that this object is overridden, so
                # nothing inside it is relevant.

                # But that is not necessarily correct, '$ref' just inserts the new parts, does not override missing
                # fields. Therefore, we could find references deeper in this object.

            next_path = f'{current_path}.{k}'
            extract_references_single(in_args, v, schema_key_in_decomposed_objects, reference_paths, next_path)
        return

    # If this is an array '[]', we must construct an array-like path (a[5])
    if isinstance(current_node, list):
        for i, e in enumerate(current_node):
            extract_references_single(in_args, e, schema_key_in_decomposed_objects, reference_paths, f'{current_path}[{i}]')


def extract_references(in_args: Input, contents_list: list[tuple[str, dict[str, Any]]],
                       reference_paths: list[tuple[str, str]]) -> None:
    """
    From a given list of dict-JSON schemas, parses each and collects paths and objects containing a '$ref' field.
    :param in_args: script input args.
    :param contents_list: list of JSON schemas.
    :param reference_paths: list to collect to.
    :return: None.
    """
    for root_id, content in contents_list:
        extract_references_single(in_args, content, root_id, reference_paths)


def filter_meta_properties(contents: dict[str, Any]) -> dict[str, Any]:
    """
    Creates a new dict of JSON schema without undesired meta tags. Keeps '$comment' and '$ref' tags.
    This only applies at the root level, where we would find '$defs', '$schema', '$id'.
    :param contents: dict containing JSON schema.
    :return: dict of filtered decomposed JSON schema.
    """
    return {k: v for k, v in contents.items() if
            not k.startswith('$') or k.startswith('$comment') or k.startswith('$ref')}


def decompose_single(in_args: Input, this_relative_path: str, this_contents: dict[str, Any],
                     reference_paths: list[tuple[str, str]],
                     decomposed_objects: dict[str, dict[str, Any]], origins: dict[str, str]) -> None:
    """
    For the current 'this_contents' root JSON schema:
      - Removes meta keys ($id, $schema, $defs, ...) and stores its actual contents in `decomposed_objects`, at
        the 'this_relative_path' key
      - Stores JSON pointers to any object that has a '$ref' key. This is collected in 'reference_paths'
      - For each object in '$defs':
        - stores it in 'decomposed_objects' at the key it appears at in '$defs'
        - stores the origin of this '$def' subschema in 'origins', keeping track of where it comes from,
          to be re-attached at bundling.
        - Stores JSON pointers to any object that has a '$ref' key. This is collected in 'reference_paths'

    :param in_args: script input args.
    :param this_relative_path: relative path of this root JSON schema.
    :param this_contents: dict containing this root JSON schema contents, before decomposition.
    :param reference_paths: list to store path of referencing object -> object containing '$ref'.
    :param decomposed_objects: dict of key -> decomposed object (no meta args, no sub schemas in '$defs').
    :param origins: dict of key -> origin key. Tracks subschema origins.
    :return: None
    """

    if this_relative_path in decomposed_objects:
        raise ValueError(f"Unexpected '{this_relative_path}' in already decomposed objects.")

    decomposed_objects[this_relative_path] = filter_meta_properties(this_contents)
    # Objects to parse for references after decomposition.
    decomposed_objects_to_parse_for_references: list[tuple[str, dict[str, Any]]] = [
        (this_relative_path, decomposed_objects[this_relative_path])]

    if in_args.verbose:
        print(f"-- Registered decomposed schema '{this_relative_path}' from '{this_relative_path}'")

    if '$defs' in this_contents:
        for subschema_key, subschema_contents in this_contents['$defs'].items():
            if subschema_key in decomposed_objects:
                raise ValueError(f"Unexpected '{subschema_key}' in already decomposed objects.")
            decomposed_objects[subschema_key] = filter_meta_properties(subschema_contents)
            decomposed_objects_to_parse_for_references.append((subschema_key, decomposed_objects[subschema_key]))
            origins[subschema_key] = this_relative_path
            if in_args.verbose:
                print(f"-- Registered decomposed schema '{subschema_key}' from '{this_relative_path}'")

    extract_references(in_args, decomposed_objects_to_parse_for_references, reference_paths)


def decompose(in_args: Input, schema_relative_paths_and_contents: list[tuple[Path, dict[str, Any]]]) -> tuple[
    list[tuple[str, str]], dict[str, dict[str, Any]], dict[str, str]]:
    """
    Extracts relevant objects from all the detected schemas. For each JSON:
      - Removes meta keys ($id, $schema, $defs, ...) and stores its actual contents in `decomposed_objects`, at
        the 'relative-path' key
      - Stores JSON pointers to any object that has a '$ref' key. This is collected in 'reference_paths'.
      - For each object in '$defs':
        - stores it in 'decomposed_objects' at the key it appears at in '$defs'.
        - stores the origin of this '$def' subschema in 'origins', keeping track of where it comes from,
          to be re-attached at bundling.
        - Stores JSON pointers to any object that has a '$ref' key. This is collected in 'reference_paths'
    :param in_args: input args
    :param schema_relative_paths_and_contents: list of relative path -> dict of JSON contents.
    :return: tuple of 'reference_paths', 'decomposed_objects' and 'origins'.
    """
    reference_paths = []
    decomposed_objects = {}
    origins = {}

    for relative_path, contents in schema_relative_paths_and_contents:
        decompose_single(in_args, str(relative_path), contents, reference_paths, decomposed_objects, origins)

    return reference_paths, decomposed_objects, origins


def get_object_at_json_pointer(json: dict[str, Any], path: str) -> dict[str, Any]:
    """
    Obtains JSON object at a given JSON pointer.
    :param json: object to access.
    :param path: pointer to location.
    :return: object, if any found.
    """
    segments = [s for s in re.split(r'[.\\[\]]', path) if s]
    assert segments[0] == '$'

    node = json
    for segment in segments[1:]:
        if not isinstance(node, dict) and not isinstance(node, list):
            raise ValueError(f"Path '{path}' in json did not refer to a valid indexable object.")

        if isinstance(node, dict):
            node = node[segment]
        else:
            node = node[int(segment)]

    if not isinstance(node, dict):
        raise ValueError(f"Path '{path}' in json does not refer to a referenceable object")
    return node


def get_decomposed_key_at_content(root: str, complete: str) -> str:
    """
    Obtains relative path from content root.
    :param root: content root.
    :param complete: absolute path.
    :return: relative path.
    """
    if not complete.startswith(root):
        raise ValueError(f'Reference {complete} does not map to content root {root}')
    return complete.removeprefix(root)


def escape_json_ref_path(path: str) -> str:
    """
    Escaped JSON path to a key that can be used into '$defs'. Cannot store '/' as key there.
    :param path: to convert
    :return: escaped path
    """
    return re.sub('/', '__', path)


def de_escape_json_ref_path(path: str) -> str:
    """
    Reverts escaped JSON path from a key that can be used into '$defs'.
    :param path: escaped path to convert
    :return: original path
    """
    return re.sub('__', '/', path)


def json_pointer_from_path(path: str) -> str:
    """
    Converts a JSON path into a JSON pointer.
    Converts "a/b/3/c" into "$.a.b[3].c"
    :param path: to convert
    :return: JSON pointer
    """
    return f"$.{'.'.join(path.split('/'))}"


def instantiate_defs_originating_from_schema(in_args: Input, bundled: dict[str, Any], key_of_this_schema: str,
                                             subschemas: list[tuple[str, dict[str, Any]]], origins: dict[str, str],
                                             decomposed_schemas: dict[str, dict[str, Any]]) -> None:
    """
    Creates subschemas in '$defs' for object originating from 'key_of_this_schema'.
    :param in_args: script input args.
    :param bundled: current JSON root schema being bundled.
    :param key_of_this_schema: key of schema to instantiate subschemas for.
    :param subschemas: list of key -> subschema object, to insert new subschemas into.
           Required to parse for references later.
    :param origins: list of subschema key -> schema key, used to re-attach '$defs' to objects.
    :param decomposed_schemas: dict of schema key -> decomposed schema. Without meta elements or subschemas.
    :return:
    """
    for def_originating_from_here in [d for d, v in origins.items() if v == key_of_this_schema]:
        assert def_originating_from_here in decomposed_schemas
        if '$defs' not in bundled:
            bundled['$defs'] = {}

        # Create a copy of this schema, as to not alter contents of 'decomposed_schemas'
        copied_subschema = deepcopy(decomposed_schemas[def_originating_from_here])
        # Ignore 'version' fields in subobjects as it might confuse schema-schema validator.
        if 'version' in copied_subschema:
            del copied_subschema['version']

        bundled['$defs'][def_originating_from_here] = copied_subschema
        subschemas.append((def_originating_from_here, copied_subschema))

        if in_args.verbose:
            print(f"---- Re-inserted '$def' for '{def_originating_from_here}'")


def replace_references(in_args: Input, bundled: dict[str, Any], key_of_this_schema: str, this_schema: dict[str, Any],
                       content_root: str,
                       reference_paths: list[tuple[str, str]], decomposed_schemas: dict[str, dict[str, Any]], origins: dict[str, str]) -> None:
    """
    Replaces references ('$ref') in current object to external URLs with references to local object in '$defs'.
    :param in_args: script input args.
    :param bundled: current JSON root schema being bundled.
    :param key_of_this_schema: path of the object containing a '$ref', inside the root schema.
    :param this_schema: object containing a '$ref' field.
    :param content_root: string containing URL content root.
    :param reference_paths: list of JSON schema path -> object containing a '$ref' marker.
    :param decomposed_schemas: dict of schema key -> decomposed schema. Without meta elements or subschemas.
    :param origins: list of subschema key -> schema key, used to re-attach '$defs' to objects.
    :return: None
    """

    # Look through what we know are potential referencer objects, extracted at parse.
    for ref_path, key_of_referencing_object in reference_paths:
        if key_of_referencing_object != de_escape_json_ref_path(key_of_this_schema):
            continue

        # If this instance is one of them
        referencing_object = get_object_at_json_pointer(this_schema, ref_path)
        if referencing_object['$ref'].startswith('#'):
            # If this uses a '$ref' to a local object, just ignore it, as it will be copied when acquiring the root
            # object (if '$ref' is originating from another object, it's because it was copied here as well, so
            # its defs will exist).
            if referencing_object['$ref'] == '#' and bundled is not this_schema:
                # Except self-reference, in that case, we want to reference the added sub-schema.
                origin_key = key_of_this_schema
                if origin_key in origins:
                    origin_key = origins[origin_key]

                referencing_object['$ref'] = f'#/$defs/{escape_json_ref_path(origin_key)}'

            continue

        key_of_referenced_object = get_decomposed_key_at_content(content_root, referencing_object['$ref'])

        # $ref might be URL#/$defs/subpath. Therefore, we are interested in copying just the subobject (in that case).
        key_parts = key_of_referenced_object.split('#')
        assert len(key_parts) > 0
        key_of_referenced_object = key_parts[0]
        path_in_referenced_object = key_parts[1] if len(key_parts) > 1 else ''

        # Should exist at this point, but regardless.
        if key_of_referenced_object not in decomposed_schemas:
            raise ValueError(
                f"Undefined reference to '{key_of_referenced_object}' in '{key_of_this_schema}' at '{ref_path}'")

        if '$defs' not in bundled:
            bundled['$defs'] = {}

        if not path_in_referenced_object:
            # If URL
            referencing_object['$ref'] = f'#/$defs/{escape_json_ref_path(key_of_referenced_object)}'
        else:
            # If URL#/$defs/subpath
            referencing_object['$ref'] = f'#/$defs/{escape_json_ref_path(path_in_referenced_object.split('/')[-1])}'

        if in_args.verbose:
            print(f"---- Replacing reference to '{key_of_referenced_object}' at '{ref_path}'")

        # Skip instantiation in $defs if already here.
        if key_of_referenced_object in bundled['$defs'] and not path_in_referenced_object:
            if in_args.verbose:
                print(f"-- Skipping duplicate of definition for '{key_of_referenced_object}'")
            continue

        # By default, copy whole referred-to object.
        referenced_object = decomposed_schemas[key_of_referenced_object]
        if path_in_referenced_object and path_in_referenced_object.startswith('/$defs/'):
            # If URL#/$defs/subpath, then we copy the subobject only.
            path_in_referenced_object = path_in_referenced_object.removeprefix('/$defs/')
            remaining_segments = path_in_referenced_object.split('/', maxsplit=1)

            referenced_object = get_object_at_json_pointer(decomposed_schemas[remaining_segments[0]],
                                                           json_pointer_from_path(remaining_segments[1] if len(
                                                            remaining_segments) > 1 else ''))

        # Do a full copy of the subschema, place it in '$defs'.
        copied_subobject = deepcopy(referenced_object)

        # Ignore 'version' root fields in subobjects as it might confuse schema-schema validator.
        if 'version' in copied_subobject:
            del copied_subobject['version']

        if not path_in_referenced_object:
            bundled['$defs'][escape_json_ref_path(key_of_referenced_object)] = copied_subobject
        else:
            # Take the last item in the path ($defs/item <-- only this)
            # Yes, it is assuming that there are only two items in the path, but this is the most encountered
            # case. Usually we refer to only subschemas (URL#/$defs/here).
            # If we need objects in subschemas (URL#/$defs/not_here/but_here), this should be changed.
            bundled['$defs'][escape_json_ref_path(path_in_referenced_object.split('/')[-1])] = copied_subobject

        if in_args.verbose:
            print(f"---- Inserted '$def' for '{key_of_referenced_object}'")


def bundle_single(in_args: Input, decomposed_key: str, content_root: str,
                  reference_paths: list[tuple[str, str]], decomposed_schemas: dict[str, dict[str, Any]],
                  output: dict[str, dict[str, Any]], origins: dict[str, str]) -> None:
    """
    Bundles a single JSON schema object. Adds objects in '$defs' as follows:
      - Any objects that were originally in '$defs'
      - For each '$ref' in the schema, adds the referenced object in '$defs' and changes the '$ref' path to refer to
        the path in '$defs' instead of the URL.
      - Then does the same for each object in '$defs'.
        Since new objects can appear in '$defs', with new '$ref' fields, this process is done repeatedly, an arbitrary
        number of times (3 here). Dumb approach, but it works.
      - An exception is done for paths referencing to a URL's subschema (URL#/$defs/...).
        In this case, we do not use the complete schema in the URL, just the subschema.

    We have to resolve references here, instead of in decomposed_objects, because we can have accidental recursion in
    schemas.

    :param in_args: script input args.
    :param decomposed_key: key of this schema in 'decomposed_objects'.
    :param content_root: string containing URL content root.
    :param reference_paths: list of JSON schema path -> object containing a '$ref' marker.
    :param decomposed_schemas: dict of schema key -> decomposed schema. Without meta elements or subschemas.
    :param output: dict of relative_path -> bundled JSON schema to store the bundled output into.
    :param origins: list of subschema key -> schema key, used to re-attach '$defs' to objects.
    :return: None.
    """
    assert decomposed_key in decomposed_schemas

    # First, make sure we create an actual copy of the decomposed schema, so we do not override it for other users.
    bundled = deepcopy(decomposed_schemas[decomposed_key])

    if in_args.verbose:
        print(f"-- Bundling schema '{decomposed_key}'")

    # Keep track of subschemas instantiated in '$defs' to resolve their references later.
    subschemas: list[tuple[str, dict[str, Any]]] = []

    # Create defs for this schema, and replace refs found in current object.
    instantiate_defs_originating_from_schema(in_args, bundled, decomposed_key, subschemas, origins, decomposed_schemas)
    replace_references(in_args, bundled, decomposed_key, bundled, content_root, reference_paths, decomposed_schemas, origins)

    # And do this a bunch of times. More references could have been inserted by the new subschemas.
    for subschema_key, subschema_contents in subschemas:
        replace_references(in_args, bundled, subschema_key, subschema_contents, content_root, reference_paths,
                           decomposed_schemas)

    # And try to instantiate + resolve a couple of times again, only for objects inside '$defs'
    num_of_parses = 3
    while num_of_parses > 0:
        if '$defs' in bundled:
            old_keys = [k for k in bundled['$defs']]
            for k in old_keys:
                de_escaped_key = de_escape_json_ref_path(k)
                instantiate_defs_originating_from_schema(in_args, bundled, de_escaped_key, subschemas, origins,
                                                         decomposed_schemas)
                replace_references(in_args, bundled, k, bundled['$defs'][k], content_root, reference_paths,
                                   decomposed_schemas, origins)

        for subschema_key, subschema_contents in subschemas:
            replace_references(in_args, bundled, subschema_key, subschema_contents, content_root, reference_paths,
                               decomposed_schemas, origins)
        num_of_parses -= 1

    output[decomposed_key] = bundled
    pass


def bundle(in_args: Input, paths_of_schemas_to_bundle: list[Path], content_root: str,
           reference_paths: list[tuple[str, str]], decomposed_schemas: dict[str, dict[str, Any]],
           origins: dict[str, str]) -> dict[
    str, dict[str, Any]]:
    """
    For each path of output schema in 'paths_of_schemas_to_bundle', composes the bundled output JSON schema.
    :param in_args: script input args.
    :param paths_of_schemas_to_bundle: list of paths relative to input/output dir of schemas to bundle.
    :param content_root: string containing content root of URLs in schemas. Used to break down URLs to relative paths
           in references.
    :param reference_paths: list containing paths to objects containing '$ref' tags.
    :param decomposed_schemas: dict of key to decomposed object.
    :param origins: dict of key of subschema to origin schema.
    :return: dict of relative path in output_dir -> bundled JSON schema.
    """
    bundled_schemas_by_paths = {}
    for path_to_schema_to_bundle in paths_of_schemas_to_bundle:
        if str(path_to_schema_to_bundle) in decomposed_schemas:
            bundle_single(in_args, str(path_to_schema_to_bundle), content_root, reference_paths, decomposed_schemas,
                          bundled_schemas_by_paths, origins)

    return bundled_schemas_by_paths


def write_single_bundled_schema(in_args: Input, path: Path, schema: dict[str, Any]) -> None:
    """
    Writes current schema to 'in_args.out_dir/path'. If parent directory does not exist, it is created.
    If parent path exists, but it is not a directory, errors out.
    :param in_args: script input args.
    :param path: path relative to in_args.out_dir to write JSON into.
    :param schema: schema to write.
    :return: None.
    """
    from os import makedirs
    from json import dump

    if in_args.verbose:
        print(f"-- Writing bundled schema at '{path}'")

    if not path.parent.exists():
        makedirs(path.parent)

    if not path.parent.is_dir():
        raise ValueError(f"Error: output path at '{path.parent}' is not a directory")

    with open(path, mode='w') as file:
        dump(schema, fp=file, indent=2)


def write_bundled_schemas(in_args: Input, bundled_schemas: dict[str, dict[str, Any]]) -> None:
    """
    For each bundled schema, writes it in the path 'output_dir/path_relative_to_input_dir' to preserve structure.
    :param in_args: script input args.
    :param bundled_schemas: dict of input relative path -> bundled JSON contents.
    :return: None.
    """
    for rel_path, schema in bundled_schemas.items():
        path_to_write_to = in_args.out_dir / rel_path
        write_single_bundled_schema(in_args, path_to_write_to, schema)


def main(arguments: list[str]) -> None:
    from argparse import ArgumentParser

    prog_name = 'json_schema_bundler.py'
    arg_parser = ArgumentParser(prog='json_schema_bundler.py',
                                description='Script used to generate bundled schemas (without external references).')
    arg_parser.add_argument('-i', '--input-directory', nargs=1, metavar='/path/to/input/directory', required=True,
                            help='Path to the directory to parse')
    arg_parser.add_argument('-o', '--output-directory', nargs=1, metavar='/path/to/output/directory', required=False,
                            default='.', help='Path to the output directory. Defaults to current working directory.')
    arg_parser.add_argument('schemas', nargs='+', metavar='schema_file.json',
                            help='Names of schema files to look for and bundle from the input directory. Preserves directory structure in output directory.')
    arg_parser.add_argument('-v', '--verbose', action='store_true', required=False, default=False,
                            help='Enable verbose logging')

    parsed_arguments = vars(arg_parser.parse_args(arguments))
    parsed_arguments['schemas'] = [s for s in parsed_arguments['schemas'] if not s.endswith(prog_name)]

    in_args = validate_input(parsed_arguments)
    if in_args.verbose:
        print(f"Parsing directory '{in_args.in_dir}'...")

    paths_of_all_schemas_relative_to_input_dir = acquire_paths_of_schemas_relative_to_input_dir(in_args)
    paths_of_schemas_to_bundle = acquire_paths_of_schemas_to_bundle(in_args, paths_of_all_schemas_relative_to_input_dir)

    if not paths_of_schemas_to_bundle:
        raise ValueError(
            f"No schema from given input schemas '{in_args.files}' found in input directory {in_args.in_dir}")

    if in_args.verbose:
        print(f'All identified schemas:')
        for s in paths_of_all_schemas_relative_to_input_dir:
            print(f'  - {s}')

        print(f'Schemas to bundle:')
        for s in paths_of_schemas_to_bundle:
            print(f'  - {s}')

    parsed_schemas = parse_schemas(in_args, paths_of_all_schemas_relative_to_input_dir)
    content_root = locate_content_root(in_args, parsed_schemas)

    if in_args.verbose:
        print('\nParsing input schemas:')
    reference_paths, decomposed_schemas, origins = decompose(in_args, parsed_schemas)

    if in_args.verbose:
        print('\nBundling output schemas:')
    rebundled_schemas = bundle(in_args, paths_of_schemas_to_bundle, content_root, reference_paths, decomposed_schemas,
                               origins)

    if in_args.verbose:
        print(f"\nWriting bundled schemas at '{in_args.out_dir}'")

    write_bundled_schemas(in_args, rebundled_schemas)

    pass


if __name__ == '__main__':
    from sys import argv

    main(argv)
