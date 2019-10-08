#!/usr/bin/fish
set data_dir $HOME/.local/share/omf/omf/data
set backup_dir $HOME/Backups/omf/omf/data

mkdir -p $backup_dir
rm -fr $backup_dir/*

cp -r $data_dir/* $backup_dir

