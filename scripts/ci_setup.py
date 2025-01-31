#!/usr/bin/env python3

from argparse import ArgumentParser
from logging import getLogger

import os
import sys

from subprocess import check_output

from performance.common import get_repo_root_path
from performance.common import get_tools_directory
from performance.common import push_dir
from performance.common import validate_supported_runtime
from performance.logger import setup_loggers

import dotnet
import micro_benchmarks

global_extension = ".cmd" if sys.platform == 'win32' else '.sh'

def init_tools(
        architecture: str,
        dotnet_versions: str,
        target_framework_monikers: list,
        verbose: bool) -> None:
    '''
    Install tools used by this repository into the tools folder.
    This function writes a semaphore file when tools have been successfully
    installed in order to avoid reinstalling them on every rerun.
    '''
    getLogger().info('Installing tools.')
    channels = [
        micro_benchmarks.FrameworkAction.get_channel(
            target_framework_moniker)
        for target_framework_moniker in target_framework_monikers
    ]
    dotnet.install(
        architecture=architecture,
        channels=channels,
        versions=dotnet_versions,
        verbose=verbose,
    )

def add_arguments(parser: ArgumentParser) -> ArgumentParser:
    '''Adds new arguments to the specified ArgumentParser object.'''

    if not isinstance(parser, ArgumentParser):
        raise TypeError('Invalid parser.')

    # Download DotNet Cli
    dotnet.add_arguments(parser)
    micro_benchmarks.add_arguments(parser)

    parser.add_argument(
        '--branch',
        dest='branch',
        required=False,
        type=str,
        help='Product branch.'
    )
    parser.add_argument(
        '--commit-sha',
        dest='commit_sha',
        required=False,
        type=str,
        help='Product commit sha.'
    )
    parser.add_argument(
        '--repository',
        dest='repository',
        required=False,
        type=str,
        help='Product repository.'
    )
    parser.add_argument(
        '--queue',
        dest='queue',
        default='testQueue',
        required=False,
        type=str,
        help='Test queue'
    )
    parser.add_argument(
        '--build-number',
        dest='build_number',
        default='1234.1',
        required=False,
        type=str,
        help='Build number'
    )
    
    parser.add_argument(
        '--locale',
        dest='locale',
        default='en-US',
        required=False,
        type=str,
        help='Locale'
    )
    parser.add_argument(
        '--perf-hash',
        dest='perf_hash',
        default='testSha',
        required=False,
        type=str,
        help='Sha of the performance repo'
    )

    parser.add_argument(
        '--get-perf-hash',
        dest="get_perf_hash",
        required=False,
        action='store_true',
        default=False,
        help='Discover the hash of the performance repository'
    )

    parser.add_argument(
        '--output-file',
        dest='output_file',
        required=False,
        default=os.path.join(get_tools_directory(),'machine-setup' + global_extension),
        type=str,
        help='Filename to write the setup script to'
    )

    # Generic arguments.
    parser.add_argument(
        '-q', '--quiet',
        required=False,
        default=False,
        action='store_true',
        help='Turns off verbosity.',
    )

    parser.add_argument(
        '--build-configs',
        dest="build_configs",
        required=False,
        nargs='+',
        default=[],
        help='Configurations used in the build in key=value format'
    )

    return parser


def __process_arguments(args: list):
    parser = ArgumentParser(
        description='Tool to generate a machine setup script',
        allow_abbrev=False,
        # epilog=os.linesep.join(__doc__.splitlines())
        epilog=__doc__,
    )
    add_arguments(parser)
    return parser.parse_args(args)

def __main(args: list) -> int:
    validate_supported_runtime()
    args = __process_arguments(args)
    verbose = not args.quiet
    setup_loggers(verbose=verbose)

    # if repository is not set, then we are doing a core-sdk in performance repo run
    # if repository is set, user needs to supply the commit_sha
    if not ((args.commit_sha is None) == (args.repository is None)):
        raise ValueError('Either both commit_sha and repository should be set or neither')

    target_framework_monikers = micro_benchmarks \
        .FrameworkAction \
        .get_target_framework_monikers(args.frameworks)

    # Acquire necessary tools (dotnet, and BenchView)
    # For arm64 runs, download the x64 version so we can get the information we need, but set all variables
    # as if we were running normally. This is a workaround due to the fact that arm64 binaries cannot run
    # in the cross containers, so we are running the ci setup script in a normal ubuntu container
    architecture = 'x64' if args.architecture == 'arm64' else args.architecture

    init_tools(
        architecture=architecture,
        dotnet_versions=args.dotnet_versions,
        target_framework_monikers=target_framework_monikers,
        verbose=verbose
    )

    # dotnet --info
    dotnet.info(verbose=verbose)

    # When running on internal repos, the repository comes to us incorrectly
    # (ie https://github.com/dotnet-coreclr). Replace dashes with slashes in that case.
    repo_url = None if args.repository is None else args.repository.replace('-','/')

    variable_format = 'set %s=%s\n' if sys.platform == 'win32' else 'export %s=%s\n'
    owner, repo = ('dotnet', 'core-sdk') if args.repository is None else (dotnet.get_repository(repo_url))
    config_string = ';'.join(args.build_configs) if sys.platform == 'win32' else '"%s"' % ';'.join(args.build_configs)

    remove_dotnet = False

    output = ''

    with push_dir(get_repo_root_path()):
        output = check_output(['git', 'rev-parse', 'HEAD'])

    decoded_lines = []

    for line in output.splitlines():
        decoded_lines = decoded_lines + [line.decode('utf-8')]

    decoded_output = ''.join(decoded_lines)

    perfHash = decoded_output if args.get_perf_hash else args.perf_hash

    for framework in target_framework_monikers:
        if framework.startswith('netcoreapp'):
            if framework == 'netcoreapp3.0' or framework == 'netcoreapp5.0':
                remove_dotnet = True
            target_framework_moniker = micro_benchmarks.FrameworkAction.get_target_framework_moniker(framework)
            dotnet_version = dotnet.get_dotnet_version(target_framework_moniker, args.cli)
            commit_sha =  dotnet.get_dotnet_sdk(target_framework_moniker, args.cli) if args.commit_sha is None else args.commit_sha
            source_timestamp = dotnet.get_commit_date(target_framework_moniker, commit_sha, repo_url)

            branch = micro_benchmarks.FrameworkAction.get_branch(target_framework_moniker) if not args.branch else args.branch

            getLogger().info("Writing script to %s" % args.output_file)

            with open(args.output_file, 'w') as out_file:
                out_file.write(variable_format % ('PERFLAB_INLAB', '1'))
                out_file.write(variable_format % ('PERFLAB_REPO', '/'.join([owner, repo])))
                out_file.write(variable_format % ('PERFLAB_BRANCH', branch))
                out_file.write(variable_format % ('PERFLAB_PERFHASH', perfHash))
                out_file.write(variable_format % ('PERFLAB_HASH', commit_sha))
                out_file.write(variable_format % ('PERFLAB_QUEUE', args.queue))
                out_file.write(variable_format % ('PERFLAB_BUILDNUM', args.build_number))
                out_file.write(variable_format % ('PERFLAB_BUILDARCH', args.architecture))
                out_file.write(variable_format % ('PERFLAB_LOCALE', args.locale))
                out_file.write(variable_format % ('PERFLAB_BUILDTIMESTAMP', source_timestamp))
                out_file.write(variable_format % ('PERFLAB_CONFIGS', config_string))
                out_file.write(variable_format % ('DOTNET_VERSION', dotnet_version))
                out_file.write(variable_format % ('PERFLAB_TARGET_FRAMEWORKS', framework))

        else:
            with open(args.output_file, 'w') as out_file:
                out_file.write(variable_format % ('PERFLAB_INLAB', '0'))
                out_file.write(variable_format % ('PERFLAB_TARGET_FRAMEWORKS', framework))

    # On non-windows platforms, delete dotnet, so that we don't have to deal with chmoding it on the helix machines
    # This is only necessary for netcoreapp3.0 and netcoreapp5.0
    if sys.platform != 'win32' and remove_dotnet:
        dotnet.remove_dotnet(architecture)


if __name__ == "__main__":
    __main(sys.argv[1:])
