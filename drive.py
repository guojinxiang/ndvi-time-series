#!/usr/bin/env python
"""Helpers for interfacing with Google Drive."""

import googleapiclient.discovery
import httplib2


class DriveHelper(object):

    """A helper class for interfacing with Google Drive."""

    def __init__(self, credentials):
        """Creates a credentialed Drive service with the given credentials.

        Args:
            credentials: The OAuth2 credentials.
        """
        http = credentials.authorize(httplib2.Http())
        self.service = googleapiclient.discovery.build("drive", "v2", http=http)


    def GetExportedFiles(self, name):
        """Returns a list of Drive file objects whose title contains the name.

        Args:
            name: The string that should be in the files title. If None all files are returned.

        Returns:
            A list of Drive file objects that match the name.
        """
        if name is None:
            files = self.service.files().list(q="").execute()
        else:
            query = "title contains '%s'" % name
            files = self.service.files().list(q=query).execute()
        return files.get("items")


    def DeleteFile(self, file_id):
        """Deletes the file with the given ID.

        Args:
            file_id: The ID of the file to delete.
        """
        self.service.files().delete(fileId=file_id).execute()


    def CreatePublicFolder(self, folderName):
        """Creates a public accessible Google Drive folder.

        Args:
            folderName: The name of the folder.

        Returns:
            The Google Drive folder ID.
        """
        # create folder
        folder = self.service.files().insert(body={"title":folderName, "mimeType":"application/vnd.google-apps.folder"}).execute()
        # set permission to everyone with link
        new_permission = {"role": "reader", "type": "anyone","withLink": True}
        self.service.permissions().insert(fileId=folder["id"],body=new_permission).execute()
        return folder["id"]


    def RenameFile(self, file_id, new_title):
        """Renames the file with the given file ID.

        Args:
            file_id: The file ID of the file that should be renamed
            new_title: The new file name

        Returns:
            The Google Drive file ID.
        """
        f = self.service.files().update(fileId=file_id, body={"title":new_title}).execute()
        return f["id"]


    def MoveFileToFolder(self, file_id, folder_id):
        """Moves a file to a folder.

        Args:
            file_id: The file ID of the file that should be moved
            folder_id: The folder ID of the target folder

        Returns:
            The Google Drive file ID.
        """
        f = self.service.files().update(fileId=file_id, body={"parents":[{"id":folder_id}]}).execute()
        return f["id"]

    def GetDownloadUrl(self, file_id):
        """Sets the permission of a file so that everyone with a link can access it and
            returns a download url.

        Args:
            file_id: The file ID

        Returns:
            The download url
        """
        new_permission = {"role": "reader", "type": "anyone","withLink": True}
        self.service.permissions().insert(fileId=file_id,body=new_permission).execute()

        f = self.service.files().get(fileId=file_id,acknowledgeAbuse=True).execute()
        return f["webContentLink"]
