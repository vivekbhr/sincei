#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
import argparse
import numpy as np
import pandas as pd
from scipy import sparse, io
from sklearn.preprocessing import binarize
# plotting
import matplotlib
import matplotlib.pyplot as plt
matplotlib.use('Agg')
matplotlib.rcParams['pdf.fonttype'] = 42
matplotlib.rcParams['svg.fonttype'] = 'none'

# single-cell stuff
import anndata
import scanpy as sc


## own Functions
scriptdir=os.path.abspath(os.path.join(__file__, "../../sincei"))
sys.path.append(scriptdir)
import ParserCommon
from Clustering import preprocess_adata, LSA_gensim

def parseArguments():
    plot_args = ParserCommon.plotOptions()
    other_args = ParserCommon.otherOptions()
    bc_args = ParserCommon.bcOptions()
    parser = argparse.ArgumentParser(parents=[get_args(), plot_args, other_args],
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,#argparse.RawDescriptionHelpFormatter,
        description="""
        This tool clusters the cells based on the input count matrix (output of scCountReads) and returns a
        tsv file with UMAP coordinates and corresponding cluster id for each barcode.
        """,
        usage='Example usage: scClusterCells.py -i cellCounts.h5ad -o clusters.tsv > log.txt',
        add_help=False)

    return parser

def get_args():
    parser = argparse.ArgumentParser(add_help=False)
    required = parser.add_argument_group('Required arguments')
    required.add_argument('--input', '-i',
                          metavar='LOOM',
                          help='Input file in the loom format',
                          required=True)

    required.add_argument('--outFile', '-o',
                         type=str,
                         required=True,
                         help='The file to write results to. The output file is an updated .loom object containing cell metadata, UMAP coordinates and cluster IDs.')

    general = parser.add_argument_group('Clustering Options')
    general.add_argument('--outFileUMAP', '-op',
                         type=str,
                         required=False,
                         help='The output plot file (for UMAP). If you specify this option, another file with the same '
                         'prefix (and .txt extention) is also created with the raw UMAP coordinates.')

    general.add_argument('--outFileTrainedModel', '-om',
                         type=argparse.FileType('w'),
                         required=False,
                         help='The output file for the trained LSI model. The saved model can be used later to embed/compare new cells '
                              'to the existing cluster of cells.')

    general.add_argument('--outGraph', '-og',
                         type=argparse.FileType('w'),
                         required=False,
                         help='The output file for the Graph object (lgl format) which can be used for further clustering/integration.')

    general.add_argument('--minCellSum', '-c',
                         default=1000,
                         type=float,
                         help='For filtering of cells: minimum number of regions detected in a cell for '
                               'the cell to be kept. (Default: %(default)s)')

    general.add_argument('--minRegionSum', '-r',
                         default=100,
                         type=float,
                         help='For filtering of regions: Minimum number of cells the regions should be present in, '
                              'for the region to be kept. (Default: %(default)s)')

    general.add_argument('--method', '-m',
                         type=str,
                         choices=['LSA'],
                         default='LSA',
                         help='The dimentionality reduction method for clustering. (Default: %(default)s)')

    general.add_argument('--binarize',
                         action='store_true',
                         help='Binarize the counts per region before dimentionality reduction (only for LSA/LDA)')

    general.add_argument('--nPrinComps', '-n',
                         default=20,
                         type=int,
                         help='Number of principle components to reduce the dimentionality to. '
                              'Use higher number for samples with more expected heterogenity. (Default: %(default)s)')

    general.add_argument('--nNeighbors', '-nk',
                         default=30,
                         type=int,
                         help='Number of nearest neighbours to consider for clustering and UMAP. This number should be chosen considering '
                              'the total number of cells and expected number of clusters. Smaller number will lead to more fragmented clusters. '
                              '(Default: %(default)s)')

    general.add_argument('--clusterResolution', '-cr',
                         default=1.0,
                         type=float,
                         help='Resolution parameter for clustering. Values lower than 1.0 would result in less clusters, '
                              'while higher values lead to splitting of clusters. In most cases, the optimum value would be between '
                              '0.8 and 1.2. (Default: %(default)s)')

    return parser


def main(args=None):
    args = parseArguments().parse_args(args)

    adata = anndata.read_loom(args.input)
    adata = preprocess_adata(adata, args.minCellSum, args.minRegionSum)
    #adat = lsa_anndata(adat, args.nPrinComps, args.scaleFactor)
    #adat = UMAP_clustering(adat)

    ## LSA and clustering based on gensim
    mtx = sparse.csr_matrix(adata.X.transpose())
    if args.binarize:
        mtx=binarize(mtx, copy=True)
    corpus_lsi, cell_topic, corpus_tfidf = LSA_gensim(mtx, list(adata.obs.index), list(adata.var.index), nTopics = args.nPrinComps, smartCode='lfu')
    #umap_lsi, graph = cluster_LSA(cell_topic, modularityAlg='leiden', resolution=args.clusterResolution, nk=args.nNeighbors)

    ## update the anndata object, drop cells which are not in the anndata
    adata=adata[cell_topic.index]
    adata.obsm['X_pca']=np.asarray(cell_topic.iloc[:,1:args.nPrinComps])
    #adata.obsm['X_umap']=np.asarray(umap_lsi.iloc[:,0:2])
    #adata.obs['cluster_lsi'] = [str(cl) for cl in umap_lsi['cluster']]
    #tfidf_mat = matutils.corpus2dense(corpus_tfidf, num_terms=len(corpus_tfidf.obj.idfs))
    #adata.layers['tfidf']=tfidf_mat.transpose()
    sc.pp.neighbors(adata, use_rep='X_pca', n_neighbors=args.nNeighbors)
    sc.tl.leiden(adata, resolution=args.clusterResolution)
    sc.tl.paga(adata)
    sc.pl.paga(adata, plot=False)
    sc.tl.umap(adata, min_dist=0.1, spread=5, init_pos='paga')

    adata.write_loom(args.outFile, write_obsm_varm=True)

    if args.outFileUMAP:
        ## plot UMAP
        fig=sc.pl.umap(adata, color=['leiden', 'log1p_total_counts'],
               ncols=2,  legend_loc='on data', return_fig=True, show=False)
        fig.savefig(args.outFileUMAP, dpi=200, format=args.plotFileFormat)
        #plt.rcParams['font.size'] = 8.0
        # convert cm values to inches
        #fig = plt.figure(figsize=(args.plotWidth / 2.54, args.plotHeight / 2.54))
        #fig.suptitle('LSA-UMAP', y=(1 - (0.06 / args.plotHeight)))
        #plt.scatter(umap_lsi.UMAP1, umap_lsi.UMAP2, s=5, alpha = 0.8, c=[sns.color_palette()[x] for x in list(umap_lsi.cluster)])
        #plt.tight_layout()
        #plt.savefig(args.outFileUMAP, dpi=200, format=args.plotFileFormat)
        #plt.close()
        prefix=args.outFileUMAP.split(".")[0]
        umap_lsi = pd.DataFrame(adata.obsm['X_umap'], columns=['UMAP1', 'UMAP2'], index=adata.obs.index)
        umap_lsi['cluster'] = adata.obs['leiden']
        umap_lsi.to_csv(prefix+".tsv", sep = "\t", index_label='barcode')

    # save if asked
    if args.outFileTrainedModel:
        corpus_lsi.save(args.outFileTrainedModel)
    if args.outGraph:
        graph.write_lgl(args.outGraph)

    return 0

if __name__ == "__main__":
    main()
