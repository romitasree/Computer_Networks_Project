import os
class PeerEntry:
    def __init__ (self, ip, port, start_byte, end_byte, timestamp): #peer entry will pass these parameter when creating a new peer
        self.ip = ip
        self.port = int(port)
        self.start_byte = int(start_byte)
        self.end_byte = int(end_byte)
        self.timestamp = int(timestamp)

    def __str__ (self):
        #Format: ip:port:start_byte:end_byte:timestamp
        return f"{self.ip}:{self.port}:{self.start_byte}:{self.end_byte}:{self.timestamp}" #convets peer entry object to text string 
    
class TrackerFile: #this class will take .tracker file and parse it into a tracker file object
    def init (self, filename="", filesize=0, description="", md5=""): #these are all the metadata that will be stored in the tracker file 
        self.filename = filename
        self.filesize = int(filesize)
        self.description = description
        self.md5 = md5
        self.peers = [] #list of all the peer entry will be stored in PeerEntry object

    @classmethod
    def parse_file(cls, filepath): #it will check if the file is on the disk
        tracker = cls()
        if not os.path.exists(filepath): #if not it will return an empty tracker file object
            return tracker

        with open(filepath, 'r', encoding='utf-8') as f: #opens the file to read mode 
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                if line.startswith("Filename:"): #it checks if the line starts with specific metadata and then it will parse the value, store it in the tracker file object
                    tracker.filename = line.split(":", 1)[1].strip()
                elif line.startswith("Filesize:"):
                    tracker.filesize = int(line.split(":", 1)[1].strip())
                elif line.startswith("Description:"):
                    tracker.description = line.split(":", 1)[1].strip()
                elif line.startswith("MD5:"):
                    tracker.md5 = line.split(":", 1)[1].strip()
                else:
                    parts = line.split(':') #parse peer entry line function: ip:port:start:end:timestamp
                    if len(parts) == 5:
                        peer = PeerEntry(parts[0], parts[1], parts[2], parts[3], parts[4])
                        tracker.peers.append(peer)
        return tracker
    
    def write_file(self, filepath):
        with open(filepath, 'w', encoding='utf-8') as f: #opens the file to write mode
            f.write(f"Filename: {self.filename}\n")
            f.write(f"Filesize: {self.filesize}\n")
            f.write(f"Description: {self.description}\n")
            f.write(f"MD5: {self.md5}\n")
            f.write("# list of peers follows next\n")
            for peer in self.peers:
                f.write(f"{peer.to_string()}\n") #it is writing back to the disk in the same format as it was read in
