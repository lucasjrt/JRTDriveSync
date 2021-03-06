import os
import pickle

from mime_names import TYPES
from utils import load_settings

from drive_file import DriveFile

class DriveTree:
    def __init__(self, service, save_path, root):
        self.save_path = save_path
        if service is None:
            return
        self.root = DriveFile(None, root)
        self.service = service
        self.folders_hash = {}
        self.files_hash = {}

    def download(self, destination):
        '''Download all the files from the tree'''
        if not os.path.exists(destination):
            os.mkdir(destination)
        nodes = self.get_root().get_children()
        while nodes:
            node = nodes.pop(0)
            nodes = nodes + node.get_children()
            node.download(destination, self.service, recursive=False)

    def find_file(self, node_id):
        if node_id == self.root.get_id():
            return self.root
        return self.find_file_in_parent(self.root, node_id, recursive=True)

    def find_file_in_parent(self, parent, node_id, recursive=False):
        for child in parent.get_children():
            if child.get_id() == node_id:
                return child

            if recursive and child.get_children():
                ret = self.find_file_in_parent(child, node_id, True)
                if ret and ret.get_id() == node_id:
                    return ret
        return None

    def get_node_from_id(self, fileId):
        if fileId in self.folders_hash:
            return self.folders_hash[fileId]
        return None

    def get_file_count(self):
        return (len(self.files_hash) + len(self.folders_hash))

    def get_closest_nodes_from_path(self, path):
        '''Returns a list of all the nodes that are close to the path,
        of the local existing tree. It's a list because it's possible
        to have more than one file with the same name under the same
        parent, as it's not possible to distinguish them by using a
        path string.
        '''
        if path in ['root', '/root', '/root/', 'root/', '/']:
            return [self.root], ''
        path_list = [p for p in path.split('/') if p]
        current_nodes = [self.root]
        depth = 0
        for path1 in path_list:
            last_depth = depth
            for node in list(current_nodes):
                for child in node.get_children():
                    if path1 == child.get_name():
                        current_nodes.append(child)
                        if depth == last_depth:
                            depth += 1
                current_nodes.remove(node)
            if depth == last_depth:
                break
        if not current_nodes:
            current_nodes = [self.root]
        return current_nodes, '/'.join(path_list[depth:])

    def get_nodes_from_path(self, path, exclusive=True):
        '''It will return a list of nodes that fits the path. If exclusive is false,
        all of the files that are in the same parent will be added to the tree.
        '''
        closest_nodes, remaining_path = self.get_closest_nodes_from_path(path)
        if remaining_path:
            path_list = [p for p in remaining_path.split('/') if p]
            fields = 'files(name, id, mimeType, parents)'
            for p in path_list:
                for node in list(closest_nodes):
                    file_list = self.service.files().list(q="'%s' in parents and trashed = false"
                                                          % node.get_id(),
                                                          fields=fields)\
                                                          .execute().get('files', [])
                    for file1 in file_list:
                        if file1['name'] == p:
                            closest_nodes.append(DriveFile(node, file1))
                        elif not exclusive\
                        and not self.find_file_in_parent(node, file1['id']):
                            DriveFile(node, file1)
                    closest_nodes.remove(node)
                    if not closest_nodes:
                        return None
        self.save_to_file()
        return closest_nodes

    def get_path_from_id(self, fileId):
        file1 = self.service.files().get(fileId=fileId,
                                         fields='name, mimeType, parents').execute()
        isfolder = ''
        if file1['mimeType'] == TYPES['folder']:
            isfolder = '/'
        if not 'parents' in file1:
            return '?' + file1['name']
        parent = file1['parents'][0]
        if parent == self.root.get_id():
            return '/' + file1['name'] + isfolder

        return self.get_path_from_id(parent) + file1['name'] + isfolder

    def get_root(self):
        return self.root

    def load_complete_tree(self, filter_enabled=True, complete=True):
        '''Creates a folder hash table and another non-folder hash table
        stores nodes in the first hash and the id is the key (e.g. /MyDrive/Books/Fiction)
        the second one simply stores the file struct and the id is the
        key (e.g. Star Wars.pdf).

        :param filter_enabled: if whitelist or blacklist is enabled.
        :type filter_enabled: bool.
        :param complete: If will link files to tree.
        :type complete: bool.
        '''
        whitelist = blacklist = None
        if filter_enabled:
            settings = load_settings()
            if settings['whitelist-enabled']:
                whitelist = settings['whitelist-files']
            elif settings['blacklist-enabled']:
                blacklist = settings['blacklist-files']

        # =========== debug code ===========
        # just to keep local query to not request files every run
        # if not os.path.exists('folders.dat'):
        #     query = 'trashed = false and mimeType = "%s"' % TYPES['folder']
        #     fields = 'nextPageToken, files(name, id, parents, mimeType, md5Checksum)'
        #     folders_metadata = []
        #     pageToken = None
        #     while True:
        #         result = self.service.files().list(q=query,\
        #                                         fields=fields,\
        #                                         pageToken=pageToken,\
        #                                         pageSize=1000).execute()
        #         folders_metadata += result.get('files', [])
        #         pageToken = result.get('nextPageToken')
        #         if not pageToken:
        #             break
        #     with open('folders.dat', 'wb') as f:
        #         pickle.dump(folders_metadata, f, pickle.HIGHEST_PROTOCOL)
        # else:
        #     with open('folders.dat', 'rb') as f:
        #         folders_metadata = pickle.load(f)
        # =========== debug code ===========

        # =========== real code ===========
        query = 'trashed = false and mimeType = "%s"' % TYPES['folder']
        folders_metadata = []
        pageToken = None
        while True:
            fields = 'nextPageToken, files(name, id, parents, mimeType, md5Checksum)'
            result = self.service.files().list(q=query,\
                                               fields=fields,\
                                               pageToken=pageToken,\
                                               pageSize=1000).execute()
            folders_metadata += result.get('files', [])
            pageToken = result.get('nextPageToken')
            if not pageToken:
                break
        # =========== real code ===========
        # just the folders vector, will be converted to hash bellow
        folders = [f for f in folders_metadata\
                   if 'parents' in f]
        self.root.children = [] # empty tree
        stack = [] # [metadata]
        self.folders_hash = {}
        i = 0 # used to pin the node that is looking for a parent
        j = 0 # used to pin the next node that will look for the parent
        while folders or stack:
            enqueue = None
            j = 0
            for folder in folders:
                if folders[i]['parents'][0] == folder['id']:
                    enqueue = folders[i]
                    break
                j += 1

            if enqueue:
                stack.append(enqueue)
                folders.pop(i)
                if j < i:
                    i = j
                else:
                    i = j - 1
            elif folders[i]['parents'][0] == self.root.get_id():
                title = ('/' + folders[i]['name'] + '/')
                if (blacklist and title in blacklist)\
                or (whitelist and not any(title in elem for elem in whitelist)):
                    stack = []
                    folders.pop(i)
                    i = 0
                    continue
                child = DriveFile(self.root, folders[i])
                self.folders_hash[folders[i]['id']] = child

                while stack:
                    item = stack.pop()
                    title = title + '/' + item['name'] + '/'
                    if (blacklist and (title in blacklist)) \
                    or (whitelist and not \
                    any(elem in title for elem in whitelist)):
                        stack = []
                        break
                    parent = child
                    child = DriveFile(parent, item)
                    self.folders_hash[item['id']] = child
                folders.pop(i)
                i = 0
            else:
                parent_id = folders[i]['parents'][0]
                if not parent_id in self.folders_hash:
                    stack = []
                    folders.pop(i)
                    i = 0
                    continue
                elif filter_enabled:
                    title = self.folders_hash[parent_id].get_path() + folders[i]['name'] + '/'
                    if (blacklist and (title in blacklist))\
                    or (whitelist and not\
                    any(elem in title for elem in whitelist)):
                        stack = []
                        folders.pop(i)
                        i = 0
                        continue

                child = DriveFile(self.folders_hash[parent_id], folders[i])
                self.folders_hash[child.get_id()] = child
                while stack:
                    parent = child
                    item = stack.pop()
                    if (blacklist and (title in blacklist))\
                    or (whitelist and not \
                    any(elem in title for elem in whitelist)):
                        stack = []
                        break
                    child = DriveFile(parent, item)
                    self.folders_hash[item['id']] = child
                folders.pop(i)
                i = 0
        if complete:
            if self.folders_hash:
                parents_query = ['mimeType != \'%s\' and ("%s" in parents'
                                 % (TYPES['folder'], list(self.folders_hash)[0])]
                i = 0 # counter
                j = 0 # index of the list
                for item in list(self.folders_hash)[1:]:
                    adding = '"%s" in parents' % item
                    # 30000 is the max body size before too complex query
                    if len(' or ' + parents_query[j]) >= 25000:
                        parents_query[j] += ')'
                        j += 1
                        i = 0
                        parents_query.append('mimeType != \'%s\' and ("%s" in parents'
                                             % (TYPES['folder'], item))
                        continue
                    parents_query[j] += ' or ' + adding
                    i += 1
                parents_query[j] += ')'
            else:
                print('no folders found')
                return()
            fields = 'nextPageToken, files(name, id, parents, mimeType, md5Checksum)'
            pageTokens = [None] * len(parents_query)
            files_metadata = []
            while True:
                for i, query in enumerate(parents_query):
                    if pageTokens[i] != '0':
                        result = self.service.files().list(q=query,\
                                                        fields=fields,\
                                                        pageToken=pageTokens[i],\
                                                        pageSize=1000).execute()
                        files_metadata += result.get('files', [])
                        pageTokens[i] = result.get('nextPageToken')
                        if not pageTokens[i]:
                            pageTokens[i] = '0'
                if all(token == '0' for token in pageTokens):
                    break

            for metadata in files_metadata:
                if not metadata['parents'][0] == self.root.get_id():
                    parent = self.folders_hash[metadata['parents'][0]]
                else:
                    if filter_enabled:
                        continue
                    parent = self.root
                self.files_hash[metadata['id']] = DriveFile(parent, metadata)

    def load_from_file(self, file_path=None):
        '''Loads the tree from disk'''
        if not file_path:
            file_path = self.save_path

        if os.path.exists(file_path) and os.path.isfile(file_path):
            with open(file_path, 'rb') as f:
                return pickle.load(f)
        return self

    def print_folder(self, folder, level=0, depth=None):
        '''Prints the folder recusrively'''
        if depth and level == depth:
            return
        prefix = level * ('  |') + '--'
        sequence = ''
        if folder.get_sequence():
            sequence = ' (' + str(folder.get_sequence()) + ')'
        print(prefix, folder.get_name(), sequence, '\tid:', folder.get_id(), sep='')
        for child in folder.get_children():
            self.print_folder(child, level=level+1, depth=depth)

    def print_tree(self):
        self.print_folder(self.root)

    def remove_folder(self, id):
        folder = self.find_file(id)
        if folder:
            folder.get_parent.removeChildren(folder)

    def save_to_file(self, file_path=None):
        '''Saves the tree to disk'''
        if not file_path:
            file_path = self.save_path
        if not os.path.exists(os.path.split(os.path.abspath(file_path))[0]):
            os.makedirs(os.path.split(os.path.abspath(file_path))[0])

        with open(file_path, 'wb') as f:
            pickle.dump(self, f, pickle.HIGHEST_PROTOCOL)
            f.flush()
