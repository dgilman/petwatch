import config

import sqlite3
import re
import time
import hashlib
import urlparse
from tempfile import NamedTemporaryFile

import requests
from lxml import html
import twitter

TWEET = True
SAVE = True
SLEEP = True

def flip_url(url, path):
    return urlparse.urlsplit(url)._replace(path=path, query='', fragment='').geturl()

def string2int(string):
    ho = hashlib.sha512()
    ho.update(string)
    return int(ho.hexdigest(), 16) % (10 ** 8)

class Pet(object):
    def __init__(self, site, site_name, pet_id, pet_name, pet_url, img_src):
        self.site = site
        self.site_name = site_name
        self.pet_id = pet_id
        self.pet_name = pet_name
        self.pet_url = pet_url
        self.img_src = img_src

    def __unicode__(self):
        return u'Pet: {0} {1} {2} {3} {4}'.format(self.site_name, self.pet_id, self.pet_name, self.pet_url, self.img_src)

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
        if SAVE:
            self.conn.commit()

    def do_pet(self, pet):
        if self.seen(pet):
            return
        self.tweet(pet)
        self.save(pet)
        if SLEEP:
            time.sleep(5*60)

    def tweet(self, pet):
        status = u"{0}: {1} {2}".format(pet.site_name, pet.pet_name, pet.pet_url)
        if TWEET:
            # In order to work around bugs in python-twitter
            # we need to handle some things ourselves.
            fd = NamedTemporaryFile(suffix='.jpg')
            fd.write(requests.get(pet.img_src).content)
            self.api.PostUpdate(status, media=fd)
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
        et = html.fromstring(requests.get(url).text)
        pets = et.xpath('//table[@class="ResultsTable"]/tr')[1:]
        [self.do_pet(pet) for pet in pets]

        next_page = et.xpath('//a[text() = "Next Page"]')
        if len(next_page) != 0:
            self.do_page('https://petharbor.com/' + next_page[0].attrib['href'])

    def do_pet(self, pet):
        pet_url = 'https://petharbor.com/' + pet[0][0].attrib['href']
        img = pet[0][0][0]
        img_src = 'https://petharbor.com/' + img.attrib['src']
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
        et = html.fromstring(requests.get(url).text)
        pets = et.xpath('//div[@class="each_pet"]')
        [self.do_pet(pet) for pet in pets]

        np = et.xpath('//div[@class="next_prev"]/span/a')
        if len(np) != 0:
            self.do_page('https://fpm.petfinder.com/petlist/petlist.cgi' + np[0].attrib['href'])

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

class Petstablished(object):
    PET_ID = re.compile('/pets/public/(\d+)')
    def __init__(self, scraper, site, site_name, url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.url = url

    def run(self):
        et = html.fromstring(requests.get(self.url).text)
        pets = et.xpath('//div[@class="pets"]/div[@class="pet "]')
        for pet in pets:
            self.do_pet(pet)

    def do_pet(self, pet):
        pet_name = pet.xpath('div/h2')[0].text.strip()
        pet_url = pet.xpath('div/a')[0].attrib['href']
        pet_id = int(self.PET_ID.search(pet_url).group(1))
        img_src = pet.xpath('div/a/img')[0].attrib['src']
        if 'defaults' in img_src:
           return
        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, img_src)
        self.scraper.do_pet(pet)

class Rescuegroups(object):
    PET_ID = re.compile('AnimalID=(\d+)')
    def __init__(self, scraper, site, site_name, url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.url = url

    def run(self):
        et = html.fromstring(requests.get(self.url).text)
        pets = et.xpath('//div[@class="animalBrowsePanel"]/div[@class="browse"]')
        for pet in pets:
            self.do_pet(pet)

    def do_pet(self, pet):
        pet_name = pet.xpath('div/a/b')[0].text.strip()
        pet_url = flip_url(self.url, pet.xpath('div[@class="browsePicture"]/a')[0].attrib['href'])
        pet_id = int(self.PET_ID.search(pet_url).group(1))
        img_src = pet.xpath('div/a/img')[0].attrib['src']
        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, img_src)
        self.scraper.do_pet(pet)

class Grrcc(object):
    PET_ID = re.compile('post-(\d+)')
    def __init__(self, scraper, site, site_name, url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.url = url

    def run(self):
        et = html.fromstring(requests.get(self.url).text)
        pets = et.xpath('//article')
        for pet in pets:
            self.do_pet(pet)

    def do_pet(self, pet):
        pet_name = pet.xpath('descendant::a[@class="cmsms_open_link"]')[0].attrib['title'].strip()
        pet_url = pet.xpath('descendant::a[@class="cmsms_open_link"]')[0].attrib['href']
        pet_id = int(self.PET_ID.search(pet.attrib['id']).group(1))
        img_src = pet.xpath('descendant::a[@class="cmsms_image_link"]')[0].attrib['href']
        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, img_src)
        self.scraper.do_pet(pet)

class Rescuegroups2(object):
    PET_ID = re.compile('pictures/animals/\d+/(\d+)/\d+_')
    def __init__(self, scraper, site, site_name, pet_url, scrape_url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.pet_url = pet_url
        self.scrape_url = scrape_url

    def run(self):
        et = html.fromstring(requests.get(self.scrape_url).text)
        pets = et.xpath('//td[@class="rgtkSearchResultsCell"]')
        for pet in pets:
            self.do_pet(pet)

    def do_pet(self, pet):
        pet_name = pet.xpath('div[contains(concat(" ", @class, " "), " rgtkSearchPetName ")]/a')[0].text
        pet_url = self.pet_url
        img_src = pet.xpath('div/a/img')[0].attrib['src']
        pet_id = int(self.PET_ID.search(img_src).group(1))
        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, img_src)
        self.scraper.do_pet(pet)

class Cabarruscounty(object):
    PET_NAME = re.compile('Hello everyone, my name is "([^"]+)"!')
    def __init__(self, scraper, site, site_name, pet_url, scrape_url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.pet_url = pet_url
        self.scrape_url = scrape_url

    def run(self):
        et = html.fromstring(requests.get(self.scrape_url).text)
        pets = et.xpath('//div[@class="image-wrraper"]')
        for pet in pets:
            self.do_pet(pet)

    def do_pet(self, pet):
        pet_name_match = self.PET_NAME.search(pet.xpath('div/button')[0].attrib['id'])
        if pet_name_match == None:
            return
        pet_name = pet_name_match.group(1)
        pet_url = self.pet_url
        pet_id = int(pet.xpath('div')[0].text[3:])
        img_src = flip_url(self.scrape_url, pet.xpath('img')[0].attrib['src'])
        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, img_src)
        self.scraper.do_pet(pet)

class Concordhumane(object):
    def __init__(self, scraper, site, site_name, url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.url = url

    def run(self):
        self.do_page(self.url)

    def do_page(self, url):
        et = html.fromstring(requests.get(url).text)
        pets = et.xpath('//div[@class="item-list"]/ul[not(@class="pager")]/li')
        for pet in pets:
            self.do_pet(pet)
        next_link = et.xpath('//li[@class="pager-next"]')
        if len(next_link) != 0:
            self.do_page(flip_url(url, next_link[0][0].attrib['href']))

    def do_pet(self, pet):
        pet_name = self.get_pet_name(pet)
        pet_url = self.get_pet_url(pet)
        pet_id = string2int(pet_name)
        img_src = self.get_img_src(pet)
        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, img_src)
        self.scraper.do_pet(pet)

class Concordhumanecats(Concordhumane):
    def get_pet_name(self, pet):
        return pet.xpath('span/span/a')[0].text.strip()

    def get_pet_url(self, pet):
        return flip_url(self.url, pet.xpath('div/div/a')[0].attrib['href'])

    def get_img_src(self, pet):
        return pet.xpath('div/div/a/img')[0].attrib['src']

class Concordhumanedogs(Concordhumane):
    def get_pet_name(self, pet):
        return pet.xpath('span/a')[0].text.strip().split(' DR')[0]

    def get_pet_url(self, pet):
        return flip_url(self.url, pet.xpath('span/a')[0].attrib['href'])

    def get_img_src(self, pet):
        return pet.xpath('div/a/img')[0].attrib['src']


class Charlottecockerrescue(object):
    def __init__(self, scraper, site, site_name, url):
        self.scraper = scraper
        self.site = site
        self.site_name = site_name
        self.url = url

    def run(self):
        et = html.fromstring(requests.get(self.url).text)
        pets = et.xpath('//div[@class="dog-container"]/..')
        for pet in pets:
            self.do_pet(pet)

    def do_pet(self, pet):
        pet_name = pet.xpath('div/div/p')[0].text.strip()
        pet_url = flip_url(self.url, pet.attrib['href'])
        pet_id = string2int(pet_name)
        img_src = flip_url(self.url, pet.xpath('div/img')[0].attrib['src'])
        pet = Pet(self.site, self.site_name, pet_id, pet_name, pet_url, img_src)
        self.scraper.do_pet(pet)

def petwatch():
    scraper = Scraper()

    sites = []
    sites.append(PetHarbor(scraper, 1, 'Charlotte Animal Care & Control', 'https://petharbor.com/results.asp?WHERE=type_DOG&PAGE=1&searchtype=ADOPT&rows=10&imght=120&imgres=thumb&view=sysadm.v_chrl1&bgcolor=000099&text=ffffff&link=ffffff&alink=ffffff&vlink=ffffff&fontface=arial&fontsize=10&col_hdr_bg=ffffff&col_hdr_fg=0000ff&col_bg=ffffff&col_fg=000000&start=4&shelterlist=%27CHRL%27'))
    sites.append(PetHarbor(scraper, 2, 'Charlotte Animal Care & Control', 'https://petharbor.com/results.asp?WHERE=type_CAT&PAGE=1&searchtype=ADOPT&rows=10&imght=120&imgres=thumb&view=sysadm.v_chrl1&bgcolor=000099&text=ffffff&link=ffffff&alink=ffffff&vlink=ffffff&fontface=arial&fontsize=10&col_hdr_bg=ffffff&col_hdr_fg=0000ff&col_bg=ffffff&col_fg=000000&start=4&shelterlist=%27CHRL%27'))
    sites.append(PetFinder(scraper, 3, 'Humane Society of York County', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=SC76&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15'))
    sites.append(PetFinder(scraper, 4, 'Humane Society of York County', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=SC76&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15'))
    sites.append(PetFinder(scraper, 5, 'Greater Charlotte SPCA', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC691&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15'))
    sites.append(PetFinder(scraper, 6, 'Greater Charlotte SPCA', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC691&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15'))
    sites.append(PetFinder(scraper, 7, 'Humane Society of Charlotte', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC246&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15'))
    sites.append(PetFinder(scraper, 8, 'Humane Society of Charlotte', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC246&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15'))
    sites.append(PetFinder(scraper, 9, 'North Mecklenburg Animal Rescue', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC287&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15'))
    sites.append(PetFinder(scraper, 10, 'North Mecklenburg Animal Rescue', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC287&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15'))
    sites.append(PetFinder(scraper, 11, 'Cornelius Animal Shelter', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC393&status=A&age=&limit=25&offset=0&animal=Dog&title=&style=15'))
    sites.append(PetFinder(scraper, 12, 'Cornelius Animal Shelter', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC393&status=A&age=&limit=25&offset=0&animal=Cat&title=&style=15'))
    sites.append(C2CAD(scraper, 13, 'Catering to Cats & Dogs', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC626&status=A&age=&limit=25&offset=0&animal=&title=&style=15'))
    sites.append(PetFinder(scraper, 14, 'Furever Angels', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC514&status=A&age=&limit=25&offset=0&animal=&title=&style=15'))
    sites.append(PetFinder(scraper, 15, 'South Charlotte Dog Rescue', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC948&status=A&age=&limit=25&offset=0&animal=&title=&style=15'))
    sites.append(PetFinder(scraper, 16, 'S.A.F.E Animal Haven', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC371&status=A&age=&limit=25&offset=0&animal=&title=&style=15'))
    sites.append(PetFinder(scraper, 17, 'MyNextPet.com', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC65&status=A&age=&limit=25&offset=0&animal=&title=&style=15'))
    sites.append(PetFinder(scraper, 18, 'Richardson Rescue', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=SC113&status=A&age=&limit=25&offset=0&animal=&title=&style=15'))
    sites.append(PetFinder(scraper, 19, 'Faithful Friends Animal Sanctuary', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC519&status=A&age=&limit=25&offset=0&animal=&title=&style=15'))
    sites.append(PetFinder(scraper, 20, 'Ruff Life Animal Rescue', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=SC402&status=A&age=&limit=25&offset=0&animal=&title=&style=15'))
    sites.append(PetFinder(scraper, 21, 'Carolina PAWS', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC516&status=A&age=&limit=25&offset=0&animal=&title=&style=15'))
    sites.append(Petstablished(scraper, 22, 'Piedmont Animal Rescue', 'https://www.petstablished.com/organization/99949/widget/animals'))
    sites.append(Petstablished(scraper, 23, 'Lake Norman Humane', 'https://www.petstablished.com/organization/30590/widget/animals'))
    sites.append(Petstablished(scraper, 24, 'Freedom Farm Rescue', 'https://www.petstablished.com/organization/55767/widget/animals'))
#    sites.append(PetFinder(scraper, 25, 'Hope for All Dogs', 'https://fpm.petfinder.com/petlist/petlist.cgi?shelter=NC834&status=A&age=&limit=25&offset=0&animal=&title=&style=15'))
    sites.append(Rescuegroups(scraper, 26, 'Cabarrus Pets Society', 'http://www.cabarruspets.com/animals/browse'))
    sites.append(Grrcc(scraper, 27, 'Golden Retriever Rescue Club of Charlotte', 'https://grrcc.com/dogs/'))
    sites.append(Rescuegroups2(scraper, 28, 'South of the Bully', 'http://www.southofthebully.com/services.html', 'http://toolkit.rescuegroups.org/j/3/grid3_layout.php?toolkitKey=2kOov42A'))
    sites.append(Cabarruscounty(scraper, 29, 'Cabarrus County Animal Shelter', 'https://www.cabarruscounty.us/resources/availalble-for-adoption-or-rescue', 'https://sro.cabarruscounty.us/Animal_Shelter/slick/DOGS_AVAIL_AVRE.php'))
    sites.append(Cabarruscounty(scraper, 30, 'Cabarrus County Animal Shelter', 'https://www.cabarruscounty.us/resources/availalble-for-adoption-or-rescue', 'https://sro.cabarruscounty.us/Animal_Shelter/slick/CATS_AVAIL_AVRE.php'))
    sites.append(Concordhumanecats(scraper, 31, 'Humane Society of Concord', 'http://www.cabarrushumanesociety.org/browse/cat'))
    sites.append(Concordhumanedogs(scraper, 32, 'Humane Society of Concord', 'http://www.cabarrushumanesociety.org/browse/dog'))
    sites.append(Charlottecockerrescue(scraper, 33, 'Charlotte Cocker Rescue', 'http://charlottecockerrescue.com/adopt-a-cocker-spaniel.htm'))


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
