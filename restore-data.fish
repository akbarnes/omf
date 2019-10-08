#!/usr/bin/fish
set data_dir $HOME/.local/share/omf/omf/data
set backup_dir $HOME/Backups/omf/omf/data

mkdir -p $data_dir
cp -r $backup_dir/User $data_dir

mkdir -p $data_dir/Model
cp -r "$backup_dir/Model/barnes@pin3.io" $data_dir/Model

