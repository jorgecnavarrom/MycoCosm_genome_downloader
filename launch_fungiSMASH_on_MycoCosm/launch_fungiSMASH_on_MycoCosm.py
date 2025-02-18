#!/usr/bin/env python

import os
import sys
import argparse
from pathlib import Path
import subprocess
from subprocess import check_call
import time
import tempfile
from multiprocessing import Pool
import shutil
from subprocess import STDOUT

"""
Launches instances of fungiSMASH5 on fasta+gff genomes 
(intended for JGI genomes)

- Intended to use with the conda-installed version of antiSMASH
- Uses a Taxonomy file that contains the target folder following JGI's fungal 
tree

It is expected that every subfolder contains a pair of <species_id> files: one
with the assembly and the other with the gff. E.g.
    Karrh1_AssemblyScaffolds_Repeatmasked.fasta.gz
    Karrh1_GeneCatalog_genes_20140225.gff.gz
"""

__author__ = "Jorge Navarro"
__contact__ = "github.com/jorgecnavarrom"
__version__ = "v1.1"


class JGI_Project_Mini:
    """
    A reduced version of the class used to download JGI genomes
    """
    
    def __init__(self):
        self.portal = ""            # Fam[:2] + Sp[:1] + mystery number. a.k.a.: "shortname"
 
        self.assembly_file = ""
        self.gff_file = ""
        
        self.project_path = ""      # Path object
        
        return


def parameter_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--inputfolder", help="Base input folder",
        required=True, type=Path)
    parser.add_argument("-t", "--taxonomyfile", help="Tab-separated file with\
        NCBI accession (column: 'Accession), shortname (column: 'Short name')\
        and relative path to put results (column: Path')", required=True, 
        type=Path)
    #parser.add_argument("--override", help="Toggle to analyze genome even if \
        #'final' file found", default=False, action="store_true")
    parser.add_argument("-o", "--outputfolder", help="Base output directory \
        for antiSMASH results", required=True, type=Path)
    parser.add_argument("-p", "--processes", help="Parallel instances of \
        antiSMASH runs. Default: 2", type=int, default=2)
    parser.add_argument("-c", "--cpus", help="'cpus' parameter for each \
        instance of antiSMASH. Default: 2", type=int, default=2)

    return parser.parse_args()


def get_parameters():
    params = list()
    parameter_file = Path(__file__).parent / "antismash_parameters.tsv"
    if not parameter_file.is_file():
        sys.exit("Error: 'antismash_parameters.tsv' not found")
    else:
        with open(parameter_file) as f:
            for line in f:
                x = line.strip()
                if x == "" or x[0] == "#":
                    continue
                params.extend(x.split(" "))
    return params


def get_paths(i, taxonomyfile_path):
    if not taxonomyfile_path.is_file():
        sys.exit("Error: parameter --taxonomyfile does not point to a valid file")
        
    organisms = dict()
    
    print("Reading taxonomy file")
    with open(taxonomyfile_path) as f:
        header = f.readline().strip().split("\t")
        shortname_col = header.index("Short name")
        path_col = header.index("Path")
        asm_col = header.index("Assembly file")
        gff_col = header.index("GFF file")
        
        for l in f:
            line = l.strip().split("\t")
            portal = line[shortname_col]
            
            org = JGI_Project_Mini()
            org.portal = portal
            org.project_path = line[path_col]
            org.assembly_file = line[asm_col]
            org.gff_file = line[gff_col]
            organisms[portal] = org
            
            
    # quick check + sanitize:
    # Remove organisms with missing info (but taxonomy file should be complete)
    # or with info but that don't point to valid folder/files (most likely something
    # went wrong with the input folder)
    portals_to_prune = set()
    for org in organisms.values():
        if org.project_path == "":
            print(" Warning! Missing base path for {}. Skipping".format(org.portal))
            portals_to_prune.add(org.portal)
        if org.assembly_file == "" or org.gff_file == "":
            print(" Warning! Missing assembly or gff files for {}. Skipping".format(org.portal))
            portals_to_prune.add(org.portal)
            
        if not (i / org.project_path).is_dir():
            portals_to_prune.add(org.portal)
        else:
            if not (i / org.project_path / org.assembly_file).is_file():
                portals_to_prune.add(org.portal)
            if not (i / org.project_path / org.gff_file).is_file():
                portals_to_prune.add(org.portal)
            
    if len(portals_to_prune):
        for portal in portals_to_prune:
            del organisms[portal]
        print("Warning: some portals were removed (missing info/can't find files in input folder)")
            
    print(" ...done\n")
    
    return organisms


def launch_antismash(cpus, input_base_folder, output_base_folder, organism, parameters):
    portal = organism.portal
    asm_file = input_base_folder / organism.project_path / organism.assembly_file
    gff_file = input_base_folder / organism.project_path / organism.gff_file
    
    target_folder = output_base_folder / organism.project_path
    
    if not target_folder.is_dir():
        os.makedirs(target_folder, exist_ok=True)
    
    gff_type = '.gff'
    if organism.gff_file[-7:-3] == "gff3":
        gff_type = '.gff3'
            
    # "Process substitution"
    # https://stackoverflow.com/questions/15343447/bash-style-process-substitution-with-pythons-popen
    with tempfile.NamedTemporaryFile(suffix='.fasta', delete=True) as unzipped_assembly, \
            tempfile.NamedTemporaryFile(suffix=gff_type, delete=True) as unzipped_gff:
        # Decompress input fasta and gff files into the temporary files
        check_call(["zcat", asm_file], shell=False, stdout=unzipped_assembly)
        check_call(["zcat", gff_file], shell=False, stdout=unzipped_gff)
        
        cmd = []
        cmd.append("antismash")
        cmd.extend(["--cpus", str(cpus)])
        if len(parameters) > 0:
            cmd.extend(parameters)
        cmd.extend(["--output-dir", str(target_folder)])
        cmd.extend(["--logfile", str(target_folder / f"{portal}.log")])
        cmd.extend(["--genefinding-gff3", unzipped_gff.name])
        cmd.append(unzipped_assembly.name)
        # print(" ".join(cmd))
        
        #proc = subprocess.run(cmd, stderr=STDOUT, encoding="utf-8")
        proc = subprocess.run(cmd, capture_output=True, encoding="utf-8")
        try:
            proc.check_returncode()
        except subprocess.CalledProcessError as e:
            error = "\n".join([x for x in proc.stderr.split("\n") if x.startswith("ERROR")])
            print(f"Error {organism.project_path}: {error}")
            with open(output_base_folder / "Error.log", "a") as f:
                f.write(f"{portal}\t{error}\n")
        else:
            # rename all regions found
            with open(target_folder / "biosynthetic_regions_renaming.tsv", "w") as f:
                # renames all regions consecutively
                for reg, gbk in enumerate(target_folder.glob("*region*.gbk")):
                    new_name = f"{portal}.region{reg+1:03d}.gbk"
                    shutil.move(gbk, target_folder / new_name)
                    f.write(f"{gbk.name}\t{new_name}\n")
                    
            # Also rename complete annotated genome file
            genome_file = target_folder / f"{Path(unzipped_assembly.name).stem}.gbk"
            new_genome_file = target_folder / f"{portal}.gbk"
            shutil.move(genome_file, new_genome_file)
            
            json_file = target_folder / f"{Path(unzipped_assembly.name).stem}.json"
            new_json_file = target_folder / f"{portal}.json"
            shutil.move(json_file, new_json_file)

    return


def main():
    options = parameter_parser()
    
    o = options.outputfolder
    if not o.is_dir():
        os.makedirs(options.outputfolder)
        
    i = options.inputfolder
    if not i.is_dir():
        sys.exit("Error: Cannot find input folder...")
        
    # Skip everything that caused problems with antiSMASH on a previous run
    # (this strategy could change in the future)
    wgs_to_skip = set()
    if (o / "Error.log").is_file():
        print("File with errors found. These genome projects will be skipped")
        with open(o / "Error.log") as f:
            for line in f:
                wgs_to_skip.add(line.strip().split("\t")[0])
    else:
        with open(o / "Error.log", "w") as f: pass
        
    processes = options.processes
    cpus = options.cpus
    
    # Read parameter file. Each line should contain parameter-space-value
    aS_parameters = get_parameters()
    organisms = get_paths(i, options.taxonomyfile)
    
    print(f"Found {len(organisms)} genomes to work on")


    # Launch analysis of all files defined by the taxonomy file
    with Pool(processes) as pool:
        for org in organisms.values():
            if org.portal in wgs_to_skip:
                continue
            
            # Skip existing results
            if (o / org.project_path / f"{org.portal}.gbk").is_file():
                old_gbk = o / org.project_path / f"{org.portal}.gbk"
                # print(f"skip {old_gbk}")
                continue
            # antiSMASH throws an error if there are already results; (check)
            # can't over-write results for now
            
            pool.apply_async(launch_antismash, args=(cpus, i, o, org, aS_parameters, ))
        
        pool.close()
        pool.join()
        
    # Serialized version/ testing
    # for org in organisms.values():
    #     if org.portal in wgs_to_skip:
    #         continue
    #     genome_file = o / org.project_path / f"{org.portal}.gbk"
    #     #print(genome_file)
    #     if (genome_file).is_file():
    #         continue
        
    #     launch_antismash(cpus, i, o, org, aS_parameters)
    #     exit()

    
    print("Finished")


if __name__ == '__main__':
    main()
