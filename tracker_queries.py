import os
from tracker_format import TrackerFile

#List format:
#<REP LIST X>
#<1 filename1 filesize1 md5_1>
#<2 filename2 filesize2 md5_2>
#...
#<REP LIST END>

def handle_list_req(client_socket, torrents_dir="torrents"): #all the .tracker file are created and stored in torrents folder 
    if not os.path.exists(torrents_dir):
        os.makedirs(torrents_dir)

    track_files = [f for f in os.listdir(torrents_dir) if f.endswith('.track')] #find all files ending with .track
    count = len(track_files)

    response = f"<REP LIST {count}>\n" #build the response header for it

    for i, track_name in enumerate(track_files, start=1): #looks through each tracker file and find the metadata for it
        filepath = os.path.join(torrents_dir, track_name)
        tf = TrackerFile.parse(filepath)
        response += f"<{i} {tf.filename} {tf.filesize} {tf.md5}>\n"

    response += "<REP LIST END>\n"
    
    client_socket.sendall(response.encode('utf-8')) #sends the response to the peer that requested the list

#Get format:
#<REP GET BEGIN>
#<tracker file content>
#<REP GET END FileMD5>

def handle_get_req(client_socket, filename, torrents_dir="torrents"): 
    if not filename.endswith('.track'): #makes sure the filename ends with .track
        filename += '.track' #if it doesn't end with .track, it will add .track to the end of it 

    filepath = os.path.join(torrents_dir, filename)

    if not os.path.exists(filepath):
        client_socket.sendall(b"<GET file not found>\n")
        return

    tf = TrackerFile.parse(filepath) #parse the .track file into a TrackerFile object to get the metadata for it

    with open(filepath, 'r', encoding='utf-8') as f: #read the file content of the .track file
        file_content = f.read()

    response = f"<REP GET BEGIN>\n{file_content}\n<REP GET END {tf.md5}>\n" #formating the response to send back to the peer that requested the .track file
    
    client_socket.sendall(response.encode('utf-8')) #sends the response to the peer that requested the .track file
