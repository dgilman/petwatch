import config

import sqlite3
import re
import time

import requests
from lxml import html
import twitter

TWEET = True

class Pet(object):
    def __init__(self, site, site_name, pet_id, pet_name, pet_url, img_src):
        self.site = site
        self.site_name = site_name
        self.pet_id = pet_id
        self.pet_name = pet_name
        self.pet_url = pet_url
        self.img_src = img_src

    def __unicode__(self):
        return u'Pet: {0} {1} {2} {3}'.format(self.site_name, self.pet_id, self.pet_name, self.pet_url)

class Scraper(object):
    def __init__(self):
        self.conn = sqlite3.connect(config.dbname)
        self.c = self.conn.cursor()
        self.api = twitter.Api(consumer_key=config.consumer_key,
            consumer_secret=config.consumer_secret,
            access_token_key=config.access_token_key,
            access_token_secret=config.access_token_secret,
            sleep_on_rate_limit=True)

    def seen(self, pet):
        self.c.execute('SELECT 1 FROM seen WHERE site = ? AND pet = ?', (pet.site, pet.pet_id))
        rval = self.c.fetchall()
        if len(rval) == 0:
            return False
        else:
            return True

    def save(self, pet):
        self.c.execute('INSERT INTO seen (site, pet) VALUES (?, ?)', (pet.site, pet.pet_id))
        if TWEET:
            self.conn.commit()

    def do_pet(self, pet):
        if self.seen(pet):
            return
        self.tweet(pet)
        self.save(pet)
        if TWEET:
            time.sleep(5*60)

    def tweet(self, pet):
        status = u"{0}: {1} {2}".format(pet.site_name, pet.pet_name, pet.pet_url)
        if TWEET:
            self.api.PostUpdate(status, media=pet.img_src)
        else:
            print unicode(pet)

class PetHarbor(object):
    PET_ID = re.compile('ID=([^&]*)&')
    PET_NAME = re.compile('My name is (.*)[.]')

    def __init__(self, scraper, site, site_name, url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.url = url

    def run(self):
        self.do_page(self.url)

    def do_page(self, url):
        et = html.parse(url)
        pets = et.xpath('//table[@class="ResultsTable"]/tr')[1:]
        [self.do_pet(pet) for pet in pets]

        next_page = et.xpath('//a[text() = "Next Page"]')
        if len(next_page) != 0:
            self.do_page('http://petharbor.com/' + next_page[0].attrib['href'])

    def do_pet(self, pet):
        pet_url = 'http://petharbor.com/' + pet[0][0].attrib['href']
        img = pet[0][0][0]
        img_src = 'http://petharbor.com/' + img.attrib['src']
        pet_id = self.PET_ID.search(img.attrib['src']).group(1)
        try:
            pet_name = self.PET_NAME.search(pet[1].text).group(1)
        except Exception:
            pet_name = u'Unknown'

        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, img_src)
        self.scraper.do_pet(pet)

class PetFinder(object):
    PET_ID = re.compile('(\d*)$')
    def __init__(self, scraper, site, site_name, url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.url = url

    def run(self):
        self.do_page(self.url)

    def do_page(self, url):
        et = html.parse(url)
        pets = et.xpath('//div[@class="each_pet"]')
        [self.do_pet(pet) for pet in pets]

        np = et.xpath('//div[@class="next_prev"]/span/a')
        if len(np) != 0:
            self.do_page('http://fpm.petfinder.com/petlist/petlist.cgi' + np[0].attrib['href'])

    def _pet_name(self, pet):
        return pet[1][0].text.strip()

    def do_pet(self, pet):
        pet_url = pet[0][0].attrib['href'].strip()
        if not pet_url.startswith('https://'):
            pet_url = 'https://' + pet_url
        img_src = pet[0][0][1].attrib['src'].strip()
        if not img_src.startswith('https://'):
            img_src = 'https://' + img_src
        if 'camerashy' in img_src:
            return
        pet_id = self.PET_ID.search(pet_url).group(1)
        pet_name = self._pet_name(pet)


        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, img_src)
        self.scraper.do_pet(pet)

class C2CAD(PetFinder):
    def _pet_name(self, pet):
        # Delete the weird A.. thing
        name = pet[1][0].text.strip()
        if name.startswith('A..'):
            return name[3:].strip()
        return name

def petwatch():
    scraper = Scraper()

    sites = []
    sites.append(PetHarbor(scraper, 1, 'Charlotte Animal Care & Control Dogs', 'http://petharbor.com/results.asp?WHERE=type_DOG&PAGE=1&searchtype=ADOPT&rows=10&imght=120&imgres=thumb&view=sysadm.v_chrl1&bgcolor=000099&text=ffffff&link=ffffff&alink=ffffff&vlink=ffffff&fontface=arial&fontsize=10&col_hdr_bg=ffffff&col_hdr_fg=0000ff&col_bg=ffffff&col_fg=000000&start=4&shelterlist=%27CHRL%27'))
    sites.append(PetHarbor(scraper, 2, 'Charlotte Animal Care & Control Cats', 'http://petharbor.com/results.asp?WHERE=type_CAT&PAGE=1&searchtype=ADOPT&rows=10&imght=120&imgres=thumb&view=sysadm.v_chrl1&bgcolor=000099&text=ffffff&link=ffffff&alink=ffffff&vlink=ffffff&fontface=arial&fontsize=10&col_hdr_bg=ffffff&col_hdr_fg=0000ff&col_bg=ffffff&col_fg=000000&start=4&shelterlist=%27CHRL%27'))
    sites.append(PetFinder(scraper, 3, 'Humane Society of York County Dogs', 'http://fpm.petfinder.com/petlist/petlist.cgi?shelter=SC76&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15'))
    sites.append(PetFinder(scraper, 4, 'Humane Society of York County Cats', 'http://fpm.petfinder.com/petlist/petlist.cgi?shelter=SC76&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15'))
    sites.append(PetFinder(scraper, 5, 'Greater Charlotte SPCA Dogs', 'http://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC691&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15'))
    sites.append(PetFinder(scraper, 6, 'Greater Charlotte SPCA Cats', 'http://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC691&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15'))
    sites.append(PetFinder(scraper, 7, 'Humane Society of Charlotte Dogs', 'http://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC246&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15'))
    sites.append(PetFinder(scraper, 8, 'Humane Society of Charlotte Cats', 'http://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC246&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15'))
    sites.append(PetFinder(scraper, 9, 'North Mecklenburg Animal Rescue Dogs', 'http://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC287&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15'))
    sites.append(PetFinder(scraper, 10, 'North Mecklenburg Animal Rescue Cats', 'http://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC287&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15'))
    sites.append(PetFinder(scraper, 11, 'Cornelius Animal Shelter Dogs', 'http://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC393&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15'))
    sites.append(PetFinder(scraper, 12, 'Cornelius Animal Shelter Cats', 'http://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC393&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15'))
    sites.append(C2CAD(scraper, 13, 'Catering to Cats & Dogs', 'http://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC626&status=A&age=&limit=25&offset=0&animal=&title=&style=15'))


    [site.run() for site in sites]

def main():
    try:
        petwatch()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()

        import smtplib
        import email.mime.text
        msg = email.mime.text.MIMEText(tb)
        msg['Subject'] = '[petwatch] Exception while scraping: {0}'.format(str(e))
        msg['From'] = config.email_from
        msg['To'] = config.email_to
        s = smtplib.SMTP('localhost')
        s.sendmail(config.email_from, config.email_to, msg.as_string())
        s.quit()

if __name__ == "__main__":
    main()
