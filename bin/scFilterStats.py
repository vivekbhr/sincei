#!/usr/bin/env python
import argparse
import sys
import os

from deeptools import parserCommon, bamHandler, utilities
from deeptools.mapReduce import mapReduce
from deeptools.utilities import smartLabels
#from deeptools._version import __version__
from deeptoolsintervals import GTF

import numpy as np
import py2bit
import pandas as pd
## own functions
scriptdir=os.path.join(os.path.dirname(os.path.dirname(__file__)), "sincei")
sys.path.append(scriptdir)
from Utilities import *
import ParserCommon

def parseArguments():
    filterParser = ParserCommon.filterOptions()

    parser = argparse.ArgumentParser(
        parents=[filterParser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
This tool estimates the number of reads that would be filtered given a set of
settings and prints this to the terminal. Further, it tracks the number of singleton reads. The following metrics will always be tracked regardless of what you specify (the order output also matches this):

 * Total reads (including unmapped)
 * Mapped reads
 * Reads in blacklisted regions (--blackListFileName)

The following metrics are estimated according to the --binSize and --distanceBetweenBins parameters
 * Estimated mapped reads filtered (the total number of mapped reads filtered for any reason)
 * Alignments with a below threshold MAPQ (--minMappingQuality)
 * Alignments with at least one missing flag (--samFlagInclude)
 * Alignments with undesirable flags (--samFlagExclude)
 * Duplicates determined by sincei (--duplicateFilter)
 * Duplicates marked externally (e.g., by picard)
 * Singletons (paired-end reads with only one mate aligning)
 * Wrong strand (due to --filterRNAstrand)

The sum of these may be more than the total number of reads. Note that alignments are sampled from bins of size --binSize spaced --distanceBetweenBins apart.
""",
        usage='Example usage: estimateReadFiltering.py -b sample1.bam sample2.bam > log.txt')

    required = parser.add_argument_group('Required arguments')
    required.add_argument('--bamfiles', '-b',
                          metavar='FILE1 FILE2',
                          help='List of indexed bam files separated by spaces.',
                          nargs='+',
                          required=True)
    required.add_argument('--barcodes',
                           help="A single-column file containing barcodes (whitelist) to be used",
                           metavar="TXT",
                           required=True)

    general = parser.add_argument_group('General arguments')

    general.add_argument('--outFile', '-o',
                         type=parserCommon.writableFile,
                         help='The file to write results to. By default, results are printed to the console')

    general.add_argument('--labels', '-l',
                         help='Labels for the samples. The '
                         'default is to use the file name of the '
                         'sample. The sample labels should be separated '
                         'by spaces and quoted if a label itself'
                         'contains a space E.g. --labels label-1 "label 2"  ',
                         nargs='+')

    general.add_argument('--smartLabels',
                         action='store_true',
                         help='Instead of manually specifying labels for the input '
                         'BAM files, this causes sincei to use the '
                         'file name after removing the path and extension.')

    general.add_argument('--binSize', '-bs',
                         metavar='INT',
                         help='Length in bases of the window used to sample the genome. (Default: %(default)s)',
                         default=1000000,
                         type=int)

    general.add_argument('--distanceBetweenBins', '-n',
                         metavar='INT',
                         help='To reduce the computation time, not every possible genomic '
                         'bin is sampled. This option allows you to set the distance '
                         'between bins actually sampled from. Larger numbers are sufficient '
                         'for high coverage samples, while smaller values are useful for '
                         'lower coverage samples. Note that if you specify a value that '
                         'results in too few (<1000) reads sampled, the value will be '
                         'decreased. (Default: %(default)s)',
                         default=10000,
                         type=int)

    general.add_argument('--numberOfProcessors', '-p',
                         help='Number of processors to use. Type "max/2" to '
                         'use half the maximum number of processors or "max" '
                         'to use all available processors. (Default: %(default)s)',
                         metavar="INT",
                         type=parserCommon.numberOfProcessors,
                         default=1,
                         required=False)

    general.add_argument('--verbose', '-v',
                         help='Set to see processing messages.',
                         action='store_true')

#    general.add_argument('--version', action='version',
#                         version='%(prog)s {}'.format(__version__))

    filtering = parser.add_argument_group('Optional arguments')

    filtering.add_argument('--filterRNAstrand',
                           help='Selects RNA-seq reads (single-end or paired-end) in '
                                'the given strand. (Default: %(default)s)',
                           choices=['forward', 'reverse'],
                           default=None)

    filtering.add_argument('--minMappingQuality',
                           metavar='INT',
                           help='If set, only reads that have a mapping '
                           'quality score of at least this are '
                           'considered.',
                           type=int)

    filtering.add_argument('--samFlagInclude',
                           help='Include reads based on the SAM flag. For example, '
                           'to get only reads that are the first mate, use a flag of 64. '
                           'This is useful to count properly paired reads only once, '
                           'as otherwise the second mate will be also considered for the '
                           'coverage. (Default: %(default)s)',
                           metavar='INT',
                           default=None,
                           type=int,
                           required=False)

    filtering.add_argument('--samFlagExclude',
                           help='Exclude reads based on the SAM flag. For example, '
                           'to get only reads that map to the forward strand, use '
                           '--samFlagExclude 16, where 16 is the SAM flag for reads '
                           'that map to the reverse strand. (Default: %(default)s)',
                           metavar='INT',
                           default=None,
                           type=int,
                           required=False)

    filtering.add_argument('--blackListFileName', '-bl',
                           help='A BED or GTF file containing regions that should be excluded from all analyses. '
                           'Currently this works by rejecting genomic chunks that happen to overlap an entry. '
                           'Consequently, for BAM files, if a read partially overlaps a blacklisted region or '
                           'a fragment spans over it, then the read/fragment might still be considered. Please note '
                           'that you should adjust the effective genome size, if relevant.',
                           metavar="BED file",
                           nargs="+",
                           required=False)

    return parser


def getFiltered_worker(arglist):
    chrom, start, end, args = arglist
    # Fix the bounds
    if end - start > args.binSize and end - start > args.distanceBetweenBins:
        end -= args.distanceBetweenBins
    if end <= start:
        end = start + 1
    ## open genome if needed
    if args.genome2bit:
        twoBitGenome = py2bit.open(args.genome2bit, True)
    ## open blacklist file
    blackList = None
    if args.blackListFileName is not None:
        blackList = GTF(args.blackListFileName)

    o = []
    for fname in args.bamfiles:
        fh = bamHandler.openBam(fname)
        chromUse = utilities.mungeChromosome(chrom, fh.references)
        prev_pos = set()
        lpos = None

        ## a dict with barcodes = keys
        # metrics
        blacklisted = {}
        minMapq = {}
        samFlagInclude = {}
        samFlagExclude = {}
        internalDupes = {}
        externalDupes = {}
        singletons = {}
        filterRNAstrand = {}
        filterMotifs = {}
        filterGC = {}
        minAlignedFraction = {}
        # trackers
        nFiltered = {}
        total = {}  # This is only used to estimate the percentage affected
        filtered = {}

        for b in args.barcodes:
            blacklisted[b] = 0
            minMapq[b] = 0
            samFlagInclude[b] = 0
            samFlagExclude[b] = 0
            internalDupes[b] = 0
            externalDupes[b] = 0
            singletons[b] = 0
            filterRNAstrand[b] = 0
            filterMotifs[b] = 0
            filterGC[b] = 0
            minAlignedFraction[b] = 0
            nFiltered[b] = 0
            total[b] = 0  # This is only used to estimate the percentage affected


        for read in fh.fetch(chromUse, start, end):
            bc = read.get_tag(args.tagName)
            # also keep a counter for barcodes not in whitelist?
            if bc not in args.barcodes:
                continue

            filtered[bc] = 0
            if read.pos < start:
                # ensure that we never double count (in case distanceBetweenBins == 0)
                continue

            if read.flag & 4:
                # Ignore unmapped reads, they were counted already
                continue

            if args.minMappingQuality and read.mapq < args.minMappingQuality:
                filtered[bc] = 1
                minMapq[bc] += 1
            if args.samFlagInclude and read.flag & args.samFlagInclude != args.samFlagInclude:
                filtered[bc] = 1
                samFlagInclude[bc] += 1
            if args.samFlagExclude and read.flag & args.samFlagExclude != 0:
                filtered[bc] = 1
                samFlagExclude[bc] += 1

            if args.minAlignedFraction:
                if not checkAlignedFraction(read, args.minAlignedFraction):
                    filtered[bc] = 1
                    minAlignedFraction[bc] += 1

            ## reads in blacklisted regions
            if blackList and blackList.findOverlaps(chrom, read.reference_start, read.reference_start + read.infer_query_length(always=False) - 1):
                filtered[bc] = 1
                blacklisted[bc] += 1

            ## Duplicates
            if args.duplicateFilter:
                tup = getDupFilterTuple(read, bc, args.duplicateFilter)
                if lpos is not None and lpos == read.reference_start \
                        and tup in prev_pos:
                    filtered[bc] = 1
                    internalDupes[bc] += 1
                if lpos != read.reference_start:
                    prev_pos.clear()
                lpos = read.reference_start
                prev_pos.add(tup)
            if read.is_duplicate:
                filtered[bc] = 1
                externalDupes[bc] += 1
            if read.is_paired and read.mate_is_unmapped:
                filtered[bc] = 1
                singletons[bc] += 1

            ## remove reads with low/high GC content
            if args.GCcontentFilter:
                if not checkGCcontent(read, args.GCcontentFilter[0], args.GCcontentFilter[1]):
                    filtered[bc] = 1
                    filterGC[bc] += 1

            ## remove reads that don't pass the motif filter
            if args.motifFilter:
                test = [ checkMotifs(read, chrom, twoBitGenome, m[0], m[1]) for m in args.motifFilter ]
                # if none given motif found, return true
                if not any(test):
                    filtered[bc] = 1
                    filterMotifs[bc] += 1

            # filterRNAstrand
            if args.filterRNAstrand:
                if read.is_paired:
                    if args.filterRNAstrand == 'forward':
                        if read.flag & 144 == 128 or read.flag & 96 == 64:
                            pass
                        else:
                            filtered[bc] = 1
                            filterRNAstrand[bc] += 1
                    elif args.filterRNAstrand == 'reverse':
                        if read.flag & 144 == 144 or read.flag & 96 == 96:
                            pass
                        else:
                            filtered[bc] = 1
                            filterRNAstrand[bc] += 1
                else:
                    if args.filterRNAstrand == 'forward':
                        if read.flag & 16 == 16:
                            pass
                        else:
                            filtered[bc] = 1
                            filterRNAstrand[bc] += 1
                    elif args.filterRNAstrand == 'reverse':
                        if read.flag & 16 == 0:
                            pass
                        else:
                            filtered[bc] = 1
                            filterRNAstrand[bc] += 1

            total[bc] += 1
            nFiltered[bc] += filtered[bc]
        fh.close()

        # first make a tuple where each entry is a dict of barcodes:value
        tup = (total, nFiltered, blacklisted, minMapq, samFlagInclude, samFlagExclude, internalDupes, externalDupes, singletons, filterRNAstrand, filterMotifs, filterGC, minAlignedFraction)

        # now simplify it
        merged = {}
        for b in args.barcodes:
            merged[b] = tuple(merged[b] for merged in tup)
        # now merged is a dict with each key = barcode, values = tuple of stats
        # Now convert it to array
        out = np.stack([ v for k, v in merged.items() ])
        # out is an array with row = len(barcode) [384], column = len(stats) [11]
        o.append(out)
    return o


def main(args=None):
    args = parseArguments().parse_args(args)

    if args.labels and len(args.bamfiles) != len(args.labels):
        print("The number of labels does not match the number of bam files.")
        sys.exit(1)
    if not args.labels:
        if args.smartLabels:
            args.labels = smartLabels(args.bamfiles)
        else:
            args.labels = [os.path.basename(x) for x in args.bamfiles]

    ## Motif and GC filter
    if args.motifFilter:
        if not args.genome2bit:
            print("MotifFilter asked but genome (2bit) file not provided.")
            sys.exit(1)
        else:
            args.motifFilter = [ x.strip(" ").split(",") for x in args.motifFilter ]

    if args.GCcontentFilter:
        gc = args.GCcontentFilter.strip(" ").split(",")
        args.GCcontentFilter = [float(x) for x in gc]

    # open barcode file and read the content in a list
    with open(args.barcodes, 'r') as f:
        barcodes = f.read().splitlines()
    args.barcodes = barcodes

    if args.outFile is None:
        of = sys.stdout
    else:
        of = open(args.outFile, "w")

    bhs = [bamHandler.openBam(x, returnStats=True, nThreads=args.numberOfProcessors) for x in args.bamfiles]
    bhs = [x[0] for x in bhs]
    chrom_sizes = list(zip(bhs[0].references, bhs[0].lengths))
    for x in bhs:
        x.close()

    # Get the remaining metrics
    res = mapReduce([args],
                    getFiltered_worker,
                    chrom_sizes,
                    genomeChunkLength=args.binSize + args.distanceBetweenBins,
                    blackListFileName=args.blackListFileName,
                    numberOfProcessors=args.numberOfProcessors,
                    verbose=args.verbose)
    ## res, should be the list of np.arrays of length (len(barcodes) * 9)

    ## final output is an array where nrows = bamfiles*barcodes, ncol = No. of stats
    final_array = np.asarray(res).sum(axis = 0)
    ## get final row/col Names (bamnames_barcode)
    rowLabels = ["{}_{}".format(a, b) for a in args.labels for b in barcodes ]
    colLabels = ["Total_sampled","Filtered","Blacklisted", "Low_MAPQ",
                 "Missing_Flags","Excluded_Flags","Internal_Duplicates",
                 "Marked_Duplicates","Singletons","Wrong_strand",
                 "Wrong_motif", "Unwanted_GC_content", "Low_aligned_fraction"]

    final_df = pd.DataFrame(data = np.concatenate(final_array),
                  index = rowLabels,
                  columns = colLabels)
    ## since stats are approximate, present results as %
    final_df.iloc[:,1:] = final_df.iloc[:,1:].div(final_df.Total_sampled, axis=0)*100

    if args.outFile is not None:
        final_df.to_csv(args.outFile, sep = "\t")
    else:
        print(final_df)

    return 0

if __name__ == "__main__":
    main()
