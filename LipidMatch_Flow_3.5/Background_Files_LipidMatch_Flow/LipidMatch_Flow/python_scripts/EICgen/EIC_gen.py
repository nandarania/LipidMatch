#!python3.8
#
# Authors: [Eric Siegel <ercsgl@hotmail.com>]
# Copyright 2022 Innovative Omics
#



# imports
import argparse
import asyncio
import csv
import os
import numpy as np
import pandas
import time

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from dataclasses import dataclass
from mzXML_to_csv import parse_mzXML

"""
Convert mzXML files in the target directory to CSV format, 
and then generate EIC csv files for each feature of the target 
file, filtered to an m/z tolerance.

example command:
    python3 EIC_gen.py --mz_column 6 --rt_column 7 --mz_tolerance 0.005 --feature_id_col 12 --target_file NegIDed_FIN.csv
    --target_dir ../NTA-Tools/Test_Files

Alternatively, the EICgen class can be imported into another python file and run from there:
    import asyncio
    from EIC_gen import EICgen
    eic = EICgen(**dict(
        mz_column=6,
        rt_column=7,
        zoom_window=30,
        mz_tolerance=0.005,
        feature_id_col=12,
        target_file='NegIDed_FIN.csv',
        target_dir='../NTA-Tools/Test_Files')
    )
    asyncio.run(eic.run())


NOTE: the outfile size will be in the GB domain
"""

parser = argparse.ArgumentParser(description='Convert mzXML files in the target directory to CSV format, '
                                             'and then generate EIC csv files for each feature of the target '
                                             'file, filtered to an m/z tolerance.')
parser.add_argument('--mz_column',
                    dest='mz_column',
                    type=int,
                    help='m/z column number in the target file (column enumeration starts 1)',
                    default=6)
parser.add_argument('--rt_column',
                    dest='rt_column',
                    type=int,
                    help='RT column number in the target file (column enumeration starts 1)',
                    default=7)
parser.add_argument('--mz_tolerance',
                    dest='mz_tolerance',
                    type=float,
                    help='Tolerance for m/z values',
                    default=0.005)
parser.add_argument('--feature_id_col',
                    dest='feature_id_col',
                    type=int,
                    help='Feature ID column number in the target file (column enumeration starts 1)',
                    default=12)
parser.add_argument('--zoom_window',
                    dest='zoom_window',
                    type=float,
                    help='Zoom window used to indicate if value falls within the zoom window for a feature',
                    default=30)
parser.add_argument('--target_file',
                    dest='target_file',
                    type=str,
                    help='Name of the target file '
                         '(short name, not full path, which is assumed to be the target directory)',
                    default='NegIDed_FIN.csv')
parser.add_argument('--target_dir',
                    dest='target_dir',
                    type=str,
                    help='Path of the target directory, where the mxXML files and the target csv file are located. It '
                         'is also the output location of the EIC csv file that will be generated',
                    default='../NTA-Tools/Test_Files')


@dataclass
class Feature:
    mz: float
    RT: float
    feature_id: str


class EICgen:
    """
    Generates a single EIC csv file given a target file containing feature data,
    and a target directory containing mzXML files
    """

    def __init__(self, *args, **kwargs):
        # silence complaints about valid chained assignment use
        pandas.options.mode.chained_assignment = None

        self.max_threads = os.cpu_count() + 1
        self.mzXML_dir = kwargs['target_dir']
        self.target_file = kwargs['target_file']
        print(f"Running EIC csv generation against target file: {self.target_file}, target dir: {self.mzXML_dir}")

        # translate columns to zero indexed column numbers
        self.mz_col = kwargs['mz_column'] - 1
        self.RT_col = kwargs['rt_column'] - 1
        self.feature_id_col = kwargs['feature_id_col'] - 1
        self.tolerance = kwargs['mz_tolerance']
        self.zoom_window = kwargs['zoom_window']

        # compile a list of all mxXML files in the target directory
        self.dir_files = [os.path.abspath(os.path.join(self.mzXML_dir, f)) for f in os.listdir(self.mzXML_dir)]
        self.mzXML_files = [f for f in self.dir_files if f.split('.')[-1] == 'mzXML']

        # convert mxXML files to csv to get started
        self.mzCSV_files = self.convert_all_mzXML_to_CSV()

        # read all csv files into memory before parsing
        # (this is the current approach, other approaches
        # may be explored in the future)
        self.dfs = self.read_mzCSV_files()

    async def run(self):
        t0 = time.monotonic()
        out = os.path.join(self.mzXML_dir, date.today().strftime('%Y_%m_%d') + '_EIC_CSV.csv')
        with open(out, 'w') as EICfile:
            writer = csv.writer(EICfile)
            writer.writerow(['Feature', 'RT', 'Intensity', 'mz', 'File', 'Zoom'])

            async def write_feature_rows(f):
                rows = await self.get_feature_rows(f)
                writer.writerows(rows)

            await asyncio.wait(
                [write_feature_rows(f) for f in self.features()]
            )

        tf = time.monotonic()
        print("time: ", tf - t0)
        print('exiting EICgen...')

    @staticmethod
    def read_csv(csv_file):
        df = pandas.read_csv(csv_file)
        df['File'] = os.path.basename(csv_file)
        return df

    @staticmethod
    def chunk(rows, chunk_size):
        for i in range(0, len(rows), chunk_size):
            yield rows[i:i + chunk_size]

    def convert_all_mzXML_to_CSV(self):
        """
        Parse all mzXML files in the target directory to csv (does not recursively walk the dir),
        and return a list of the converted csv files
        """
        mzCSV_files = []
        with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            for mzXML_file in self.mzXML_files:
                out = mzXML_file.split('.')[0] + '.csv'
                mzCSV_files.append(out)
                if out in self.dir_files:
                    continue
                executor.submit(parse_mzXML, mzXML_file)
        return mzCSV_files

    def read_mzCSV_files(self):
        """
        Returns a list of tuples: csv filenames, paired with the dataframe objects
        """
        with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            mz_csv_dfs = executor.map(self.read_csv, self.mzCSV_files)
        dfs = pandas.concat(mz_csv_dfs)
        return dfs

    def features(self):
        with open(os.path.join(self.mzXML_dir, self.target_file), 'r') as NegIDfile:
            reader = csv.reader(NegIDfile, delimiter=',')
            next(reader)
            for row in reader:
                mz = float(row[self.mz_col])
                RT = float(row[self.RT_col])
                feature_id = row[self.feature_id_col]
                yield Feature(mz, RT, feature_id)

    async def get_feature_rows(self, F: Feature):
        """
        Takes a Feature object and returns a list of features rows:
        rows parsed from the mzXML files and filtered to the feature
        parameters
        """
        df = self.dfs[(self.dfs.mz < F.mz + self.tolerance) & (self.dfs.mz > F.mz - self.tolerance)]
        if not len(df):
            return []

        df['Feature'] = F.feature_id
        rt = df['rt']
        df.loc[
            np.logical_and(
                rt.lt(F.RT + self.zoom_window),
                rt.gt(F.RT - self.zoom_window)
            ), 'Zoom'
        ] = 'TRUE'
        df['Zoom'].fillna('FALSE', inplace=True)

        return df[['Feature', 'rt', 'intensity', 'mz', 'File', 'Zoom']].values


if __name__ == '__main__':
    args = parser.parse_args()
    e = EICgen(**vars(args))
    asyncio.run(e.run())
