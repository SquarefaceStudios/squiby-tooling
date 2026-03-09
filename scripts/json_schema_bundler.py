#!/usr/bin/python3
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
    if in_args.verbose:
        for file in in_args.files:
            if file not in map(lambda p: p.name, schema_paths):
                print(f'Warning: no schema matched input argument {file}')

    return [p for p in schema_paths if p.name in in_args.files]


def parse_schemas(in_args: Input, schema_paths: list[Path]) -> list[tuple[Path, dict[str, Any]]]:
    from json import load

    contents = []
    for s in schema_paths:
        with open(in_args.in_dir / s, mode='r') as f:
            contents.append((s, load(f)))

    return contents


def locate_content_root(in_args: Input, schema_relative_paths_and_contents: list[tuple[Path, dict[str, Any]]]) -> str:
    content_root = None
    content_root_obtained_from = None

    assert schema_relative_paths_and_contents

    for path, contents in schema_relative_paths_and_contents:
        if '$id' not in contents:
            raise ValueError(f"Schema at '{path}' is invalid: no root '$id' field.")

        if not content_root:
            content_root = contents['$id'].removesuffix(str(path.as_posix()))
            content_root_obtained_from = path
        else:
            current_content_root = contents['$id'].removesuffix(str(path.as_posix()))
            if content_root != current_content_root:
                raise ValueError(
                    f"Schema at '{path}' defines a different content root ('{current_content_root}') than the one identified at '{content_root_obtained_from}' (which is '{content_root}')")

    if in_args.verbose:
        print(f"Identified content root as '{content_root}'")

    return content_root


def extract_references_single(in_args: Input, current_node: Any, root_id: str, root_dict: dict[str, Any],
                              reference_paths: list[tuple[str, str]], current_path: str = '$') -> None:
    if isinstance(current_node, dict):
        for k, v in current_node.items():
            if k == '$ref':
                reference_paths.append((current_path, root_id))
                if in_args.verbose:
                    print(f"---- Identified reference in decomposed object '{root_id}' at '{current_path}'")

                continue

            next_path = f'{current_path}.{k}'
            extract_references_single(in_args, v, root_id, root_dict, reference_paths, next_path)
        return

    if isinstance(current_node, list):
        for i, e in enumerate(current_node):
            extract_references_single(in_args, e, root_id, root_dict, reference_paths, f'{current_path}[{i}]')


def extract_references(in_args: Input, contents_list: list[tuple[str, dict[str, Any]]],
                       reference_paths: list[tuple[str, str]]) -> None:
    for root_id, content in contents_list:
        extract_references_single(in_args, content, root_id, content, reference_paths)


def filter_meta_properties(contents: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in contents.items() if not k.startswith('$')}


def decompose_single(in_args: Input, this_relative_path: str, this_contents: dict[str, Any],
                     reference_paths: list[tuple[str, str]],
                     decomposed_objects: dict[str, dict[str, Any]], origins: dict[str, str]) -> None:
    if this_relative_path in decomposed_objects:
        raise ValueError(f"Unexpected '{this_relative_path}' in already decomposed objects.")

    decomposed_objects[this_relative_path] = filter_meta_properties(this_contents)
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
    reference_paths = []
    decomposed_objects = {}
    origins = {}

    for relative_path, contents in schema_relative_paths_and_contents:
        decompose_single(in_args, str(relative_path), contents, reference_paths, decomposed_objects, origins)

    return reference_paths, decomposed_objects, origins


def get_object_at_json_path(json: dict[str, Any], path: str) -> dict[str, Any]:
    segments = path.split('.')
    assert segments[0] == '$'

    node = json
    for segment in segments[1:]:
        if not isinstance(node, dict):
            raise ValueError(f"Path '{path}' in json did not refer to a valid object.")

        node = node[segment]

    return node


def get_decomposed_key_at_content(root: str, complete: str) -> str:
    if not complete.startswith(root):
        raise ValueError(f'Reference {complete} does not map to content root {root}')
    return complete.removeprefix(root)


def bundle_single(in_args: Input, decomposed_key: str, content_root: str,
                  reference_paths: list[tuple[str, str]], decomposed_schemas: dict[str, dict[str, Any]],
                  output: dict[str, dict[str, Any]], origins: dict[str, str]):
    assert decomposed_key in decomposed_schemas
    bundled = deepcopy(decomposed_schemas[decomposed_key])

    if in_args.verbose:
        print(f"-- Bundling schema '{decomposed_key}'")

    subschemas: list[tuple[str, dict[str, Any]]] = []

    for def_originating_from_here in [d for d, v in origins.items() if v == decomposed_key]:
        assert def_originating_from_here in decomposed_schemas
        if '$defs' not in bundled:
            bundled['$defs'] = {}

        copied_subschema = deepcopy(decomposed_schemas[def_originating_from_here])
        bundled['$defs'][def_originating_from_here] = copied_subschema
        subschemas.append((def_originating_from_here, copied_subschema))

        if in_args.verbose:
            print(f"---- Re-inserted '$def' for '{def_originating_from_here}'")

    for ref_path, key_of_referencing_object in reference_paths:
        if key_of_referencing_object == decomposed_key:
            referencing_object = get_object_at_json_path(bundled, ref_path)
            key_of_referenced_object = get_decomposed_key_at_content(content_root, referencing_object['$ref'])

            if key_of_referenced_object not in decomposed_schemas:
                raise ValueError(f"Undefined reference to '{key_of_referenced_object}' in '{decomposed_key}' at '{ref_path}'")

            if '$defs' not in bundled:
                bundled['$defs'] = {}

            referencing_object['$ref'] = f'#/$defs/{key_of_referenced_object}'

            if in_args.verbose:
                print(f"---- Replacing reference to '{key_of_referenced_object}' at '{ref_path}'")

            if key_of_referenced_object in bundled['$defs']:
                if in_args.verbose:
                    print(f"-- Skipping duplicate of definition for '{key_of_referenced_object}'")
                continue

            bundled['$defs'][key_of_referenced_object] = deepcopy(decomposed_schemas[key_of_referenced_object])

            if in_args.verbose:
                print(f"---- Inserted '$def' for '{key_of_referenced_object}'")

    output[decomposed_key] = bundled
    pass


def bundle(in_args: Input, paths_of_schemas_to_bundle: list[Path], content_root: str,
           reference_paths: list[tuple[str, str]], decomposed_schemas: dict[str, dict[str, Any]], origins: dict[str, str]) -> dict[
    str, dict[str, Any]]:
    bundled_schemas_by_paths = {}
    for path_to_schema_to_bundle in paths_of_schemas_to_bundle:
        if str(path_to_schema_to_bundle) in decomposed_schemas:
            bundle_single(in_args, str(path_to_schema_to_bundle), content_root, reference_paths, decomposed_schemas,
                          bundled_schemas_by_paths, origins)

    return bundled_schemas_by_paths


def write_single_bundled_schema(in_args: Input, path: Path, schema: dict[str, Any]) -> None:
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
    rebundled_schemas = bundle(in_args, paths_of_schemas_to_bundle, content_root, reference_paths, decomposed_schemas, origins)

    if in_args.verbose:
        print(f"\nWriting bundled schemas at '{in_args.out_dir}'")

    write_bundled_schemas(in_args, rebundled_schemas)

    pass


if __name__ == '__main__':
    from sys import argv

    main(argv)
