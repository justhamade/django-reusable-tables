from django.test import TestCase
from users.models.user import User, Group
from table import Table

class request:
    def __init__(self):
        self.GET = {}

class table(TestCase):
    fixtures = ["overall.json",]
    
    def testUser(self):
        # todo get this working
        pass
