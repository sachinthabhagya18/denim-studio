'use strict';

const api = '/pipeline/api/';
const find = (selector) => document.querySelector(selector);
const csrf = find('[name=csrfmiddlewaretoken]').value;
const screens = document.querySelectorAll('.screen');
const profileForm = find('#profile-form');
const profilePhotos = {
    reference_image: 'front view',
    face_reference_image: 'face close-up',
    side_reference_image: 'side profile',
    three_quarter_reference_image: 'three-quarter view',
    detail_reference_image: 'additional detail',
};
const profileSelect = find('#profile-select');
const productForm = find('#product-form');
const quickChartDialog = find('#quick-chart-dialog');
const chartSelect = find('#size-chart');
const chartSelectionStatus = find('#chart-selection-status');
const jobGrid = find('#job-list');
const labels = {
    PROCESSING: 'Processing', PENDING_DETAILS: 'Check details', PENDING_REVIEW: 'Review images',
    APPROVED: 'Approved', PUBLISHING: 'Publishing', PUBLISHED: 'Published', FAILED: 'Needs attention',
};
const stages = {
    PROCESSING: 'Waiting for the current step to finish.',
    PENDING_DETAILS: 'Check the extracted fields before starting up to three image requests.',
    PENDING_REVIEW: 'Check the three product views and original fabric close-up, and assign a size chart.',
    APPROVED: 'Product approved. Publish when it is ready for your store.',
    PUBLISHING: 'Sending this product to WooCommerce.',
    PUBLISHED: 'Edit this product and update the same WooCommerce listing when ready.',
    FAILED: 'Check the message below before retrying.',
};
const shots = {image_1_url: 'Model front', image_3_url: 'Model side', image_2_url: 'Garment front and back', fabric_image_url: 'Fabric close-up'};
const shotSteps = {image_1_url: 'model-front', image_3_url: 'model-side', image_2_url: 'garment-front-back'};
const workflowSteps = {
    extract: 'Extract product details', details: 'Confirm details', references: 'Prepare photo references', 'model-front': 'Model front',
    'model-side': 'Model side', 'garment-front-back': 'Garment front and back', fabric: 'Fabric close-up',
    review: 'Review and assign size chart', publish: 'Publish to WooCommerce', social: 'Prepare social campaign',
};
let profiles = [];
let charts = [];
let selectedProfile = null;
let job = null;
let timer;
let tab;
let jobPage = 1;
let profileRequest = 0;
let chartRequest = 0;
let jobsRequest = 0;
let reviewRequest = 0;
let regenerationJob = null;
let providerSettings = null;
let providerRequest = 0;

function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content) node.textContent = content;
    return node;
}

function notice(message, error = false) {
    const node = find('#notice');
    node.textContent = message;
    node.className = error ? 'notice error' : 'notice';
    node.hidden = !message;
}

function galleryMessage(message, error = false) {
    const node = find('#gallery-message');
    if (node.textContent !== message) node.textContent = message;
    node.classList.toggle('error', error);
    node.hidden = !message;
}

async function request(path, method = 'GET', body) {
    const headers = {'Accept': 'application/json', 'X-CSRFToken': csrf};
    if (body && !(body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
        body = JSON.stringify(body);
    }
    const response = await fetch(api + path, {method, body, headers, credentials: 'same-origin'});
    if (response.status === 204) return null;
    const isJson = response.headers.get('content-type')?.includes('application/json');
    const data = isJson ? await response.json() : null;
    if (!response.ok || !isJson) {
        const messages = (value) => typeof value === 'object' && value !== null
            ? Object.values(value).flatMap(messages) : [String(value)];
        const detail = data && messages(data).join(' ');
        const error = new Error(detail || (response.redirected || response.status === 401 || response.status === 403
            ? 'Sign in again to continue.' : 'The server could not complete this request. Try again.'));
        error.status = response.status;
        throw error;
    }
    return data;
}

function show(screen) {
    clearTimeout(timer);
    tab = screen;
    for (const section of screens) section.hidden = section.id !== screen;
    document.querySelectorAll('nav [data-tab]').forEach((button) => {
        if (button.dataset.tab === (screen === 'review' ? 'jobs' : screen)) button.setAttribute('aria-current', 'page');
        else button.removeAttribute('aria-current');
    });
    notice('');
    if (screen === 'review' || screen === 'charts') loadCharts();
}

function changeTab(screen) {
    show(screen);
    if (screen === 'jobs') loadJobs();
    if (screen === 'profiles' || screen === 'intake') loadProfiles();
    if (screen === 'image-settings') loadImageProvider();
}

function badge(state) {
    const node = element('span', 'status', labels[state] || state);
    node.dataset.state = state;
    return node;
}

function picture(url, alt) {
    const img = element('img');
    img.src = url;
    img.alt = alt;
    return img;
}

function showIntakeProfile() {
    const box = find('#intake-profile-preview');
    const item = profiles.find(({id}) => String(id) === profileSelect.value);
    box.replaceChildren();
    box.hidden = !item;
    if (!item) return;
    for (const [field, role] of Object.entries(profilePhotos)) {
        if (item[field]) box.append(picture(item[field], item.name + ' ' + role));
    }
    for (const [index, url] of (item.bulk_references || []).entries()) {
        box.append(picture(url, item.name + ' additional view ' + (index + 1)));
    }
}

async function loadProfiles() {
    const operation = ++profileRequest;
    try {
        const all = [];
        let page = 1;
        let data;
        do {
            data = await request('model-profiles/?page=' + page++);
            all.push(...data.results);
        } while (data.next);
        if (operation !== profileRequest) return;
        profiles = all;

        const selection = profileSelect.value;
        profileSelect.options.length = 1;
        profiles.forEach((item) => {
            const option = element('option', '', item.name);
            option.value = item.id;
            profileSelect.append(option);
        });
        profileSelect.value = profiles.some(({id}) => String(id) === selection) ? selection : '';
        showIntakeProfile();

        const list = find('#profile-list');
        list.replaceChildren();
        for (const item of profiles) {
            const card = element('article', 'card profile-card');
            const copy = element('div');
            const action = element('button', 'button secondary', 'Edit');
            action.onclick = () => editProfile(item);
            copy.append(element('strong', '', item.name));
            const photoCount = Object.keys(profilePhotos).filter((field) => item[field]).length + (item.bulk_references?.length || 0);
            copy.append(element('p', '', photoCount === 1 ? '1 reference photo' : photoCount + ' reference photos'));
            if (item.description) copy.append(element('p', '', item.description));
            card.append(picture(item.reference_image, item.name + ' front reference'), copy, action);
            list.append(card);
        }
        if (!profiles.length) list.append(element('p', 'card', 'No model profiles yet.'));
    } catch (error) {
        notice(error.message, true);
    }
}

function editProfile(item) {
    selectedProfile = item;
    profileForm.reset();
    profileForm.elements.name.value = item?.name || '';
    profileForm.elements.description.value = item?.description || '';
    profileForm.elements.reference_image.required = !item;
    profileForm.elements.side_reference_image.required = !item?.side_reference_image;
    const saved = find('#saved-bulk-photos');
    saved.hidden = !item?.bulk_references?.length;
    saved.textContent = item?.bulk_references?.length + ' bulk photos saved. Upload a new batch to replace that batch.';
    find('#profile-form-title').textContent = item ? 'Edit ' + item.name : 'Add a model';
    find('#save-profile').textContent = item ? 'Update profile' : 'Save profile';
    find('#cancel-profile').hidden = !item;
    if (item) profileForm.elements.name.focus();
}

profileForm.onsubmit = async (event) => {
    event.preventDefault();
    const data = new FormData(profileForm);
    for (const field of ['reference_image', 'side_reference_image']) {
        if (!data.get(field).size) data.delete(field);
    }
    const bulk = data.getAll('bulk_reference_images').filter((file) => file.size);
    if (bulk.length > 3) {
        notice('Choose up to three additional model photos.', true);
        return;
    }
    data.delete('bulk_reference_images');
    for (const file of bulk) data.append('bulk_reference_images', file);
    const current = selectedProfile;
    event.submitter.disabled = true;
    try {
        const result = await request('model-profiles/' + (current ? current.id + '/' : ''), current ? 'PATCH' : 'POST', data);
        editProfile(null);
        await loadProfiles();
        profileSelect.value = result.id;
        showIntakeProfile();
        notice('Model profile saved.');
    } catch (error) {
        notice(error.message, true);
    } finally {
        event.submitter.disabled = false;
    }
};

async function loadJobs() {
    const operation = ++jobsRequest;
    const search = find('#job-search').elements.search.value.trim();
    try {
        const data = await request('jobs/?' + new URLSearchParams({page: jobPage, search}));
        if (operation !== jobsRequest) return;
        jobGrid.replaceChildren();
        for (const item of data.results) {
            const row = element('button', 'job-row');
            const copy = element('span');
            copy.append(element('strong', '', item.final_data.title || 'Product intake'));
            copy.append(element('small', '', [item.final_data.sku, new Date(item.created_at).toLocaleDateString()].filter(Boolean).join(' · ')));
            row.append(copy, badge(item.status));
            row.onclick = () => openJob(item.job_id);
            jobGrid.append(row);
        }
        if (!data.results.length) jobGrid.append(element('p', '', 'No products match this view.'));
        find('#job-pages').hidden = !data.previous && !data.next;
        find('#job-prev').disabled = !data.previous;
        find('#job-next').disabled = !data.next;
    } catch (error) {
        notice(error.message, true);
    }
}

const jobPath = (id) => 'jobs/' + id + '/';

function renderProgress(data) {
    const progress = data.workflow_progress || {};
    const current = ({
        PENDING_DETAILS: 'details', PENDING_REVIEW: 'review', APPROVED: 'publish',
        PUBLISHING: 'publish', PUBLISHED: 'social',
    })[data.status] || progress.step || 'extract';
    const active = ['PROCESSING', 'PUBLISHING', 'FAILED'].includes(data.status);
    find('#progress-current').textContent = active ? (progress.label || workflowSteps[current])
        : current === 'social' && data.social_draft?.facebook ? 'Campaign assets ready' : workflowSteps[current];
    const model = find('#progress-model');
    model.textContent = active && progress.model ? 'AI model · ' + progress.model : '';
    const entries = Object.entries(workflowSteps);
    const currentIndex = entries.findIndex(([key]) => key === current);
    const list = find('#progress-steps');
    list.replaceChildren();
    for (const [index, [key, text]] of entries.entries()) {
        const item = element('li', '', text);
        item.dataset.state = index === currentIndex ? 'current' : index < currentIndex ? 'done' : 'waiting';
        if (key === current) item.setAttribute('aria-current', 'step');
        list.append(item);
    }
    const log = find('#workflow-log');
    const follow = log.scrollHeight - log.scrollTop - log.clientHeight < 24;
    const scrollTop = log.scrollTop;
    log.replaceChildren();
    for (const entry of progress.history || []) {
        const row = element('li');
        const stamp = element('time', '', new Date(entry.at).toLocaleTimeString());
        stamp.dateTime = entry.at;
        row.append(stamp, element('span', '', entry.label));
        if (entry.model) row.append(element('small', '', entry.model));
        log.append(row);
    }
    if (!log.childElementCount) log.append(element('li', '', 'No activity logged yet.'));
    log.scrollTop = follow ? log.scrollHeight : scrollTop;
}

function previewChart() {
    const selected = charts.find((chart) => String(chart.id) === chartSelect.value);
    const box = find('#assigned-chart');
    box.hidden = !selected;
    box.replaceChildren();
    if (selected) box.append(chartPreview(selected));
}

function chartPreview(chart) {
    if (!chart.table_rows?.length) {
        const image = picture(chart.image, chart.name);
        image.className = 'chart-image';
        image.loading = 'lazy';
        return image;
    }
    const wrapper = element('div', 'chart-table-scroll');
    const table = element('table', 'chart-table');
    table.append(element('caption', '', chart.name));
    const [headers, ...rows] = chart.table_rows;
    const header = table.createTHead().insertRow();
    for (const title of headers) {
        const cell = element('th', '', title);
        cell.scope = 'col';
        header.append(cell);
    }
    const body = table.createTBody();
    for (const row of rows) {
        const line = body.insertRow();
        for (const value of row) line.insertCell().textContent = value;
    }
    wrapper.append(table);
    return wrapper;
}

function renderCharts() {
    const selection = chartSelect.selectedIndex < 0 ? String(job?.size_chart || '') : chartSelect.value;
    chartSelect.options.length = 1;
    for (const chart of charts) {
        const option = element('option', '', chart.name);
        option.value = chart.id;
        chartSelect.append(option);
    }
    chartSelect.value = selection;
    previewChart();
    const list = find('#chart-list');
    const search = find('#chart-search').value.trim().toLowerCase();
    list.replaceChildren();
    for (const chart of charts.filter(({name}) => name.toLowerCase().includes(search))) {
        const card = element('article', 'card chart-card');
        if (!chart.table_rows?.length) card.append(element('h2', '', chart.name));
        card.append(chartPreview(chart));
        list.append(card);
    }
    if (!list.childElementCount) list.append(element('p', 'card', 'No size charts found.'));
}

async function loadCharts() {
    const operation = ++chartRequest;
    try {
        let page = 1;
        let data;
        const all = [];
        do {
            data = await request('size-charts/?page=' + page++);
            all.push(...data.results);
        } while (data.next);
        if (operation !== chartRequest) return;
        charts = all;
        renderCharts();
    } catch (error) {
        if (operation === chartRequest && (tab === 'charts' || tab === 'review')) notice(error.message, true);
    }
}

function setupReviewAssets(data, details, source) {
    const cropped = source.fabric || source.garments?.[0];
    find('#fabric-controls').hidden = !cropped || !['PENDING_REVIEW', 'APPROVED', 'PUBLISHED'].includes(data.status);
    const photo = find('#crop-source');
    if (cropped && photo.getAttribute('src') !== cropped) {
        photo.src = cropped;
        find('#fabric-form').reset();
        moveCropMarker();
    }
    const socialReady = ['PENDING_REVIEW', 'APPROVED', 'PUBLISHED'].includes(data.status) && data.image_1_url;
    find('#social-panel').hidden = !socialReady;
    const form = find('#social-form');
    if (socialReady && form.dataset.jobId !== data.job_id) {
        form.reset();
        form.dataset.jobId = data.job_id;
        for (const channel of ['facebook', 'instagram', 'tiktok']) {
            form.elements[channel].value = data.social_draft?.[channel] || data.suggested_captions?.[channel] || '';
        }
    }
    find('#logo-help').textContent = data.social_logo_configured
        ? 'Saved logo will be reused. Choose a PNG to replace it.'
        : 'Use an image with a transparent background.';
    renderSocialStates(data);
}

function renderGallery(data) {
    const images = find('#product-images');
    const canEdit = ['PENDING_REVIEW', 'APPROVED', 'PUBLISHED'].includes(data.status);
    images.replaceChildren();
    for (const [field, title] of Object.entries(shots)) {
        if (!data[field] && !canEdit) continue;
        const figure = element('figure', 'card');
        const caption = element('figcaption', '', title);
        if (canEdit) {
            const actions = element('div', 'shot-actions');
            if (shotSteps[field]) {
                const regenerate = element('button', 'button primary', data[field] ? 'Regenerate photo' : 'Generate photo');
                regenerate.type = 'button';
                regenerate.setAttribute('aria-label', (data[field] ? 'Regenerate ' : 'Generate ') + title);
                regenerate.title = field === 'image_1_url' ? 'Regenerates front and side using up to two image requests' : 'Uses one image generation request';
                regenerate.onclick = () => regenerateShot(data.job_id, field);
                actions.append(regenerate);
            } else if (data.reference_photos?.fabric || data.reference_photos?.garments?.length) {
                const crop = element('button', 'button primary', 'Adjust crop');
                crop.type = 'button';
                crop.onclick = () => {
                    const panel = find('#fabric-controls');
                    panel.open = true;
                    panel.querySelector('summary').focus();
                    panel.scrollIntoView({block: 'nearest'});
                };
                actions.append(crop);
            }
            const input = element('input', 'replacement-file');
            input.type = 'file';
            input.accept = 'image/jpeg,image/png,image/webp';
            input.setAttribute('aria-label', 'Replace ' + title);
            input.onchange = () => replacePhoto(data.job_id, field, input);
            const replace = element('button', 'button secondary', 'Upload photo');
            replace.type = 'button';
            replace.setAttribute('aria-label', 'Upload ' + title);
            replace.onclick = () => input.click();
            actions.append(replace, input);
            caption.append(actions);
        } else if (data.status === 'PROCESSING' && shotSteps[field] === data.workflow_progress?.step) {
            caption.append(element('small', 'shot-status', data.workflow_progress.label || 'Generating photo…'));
        }
        if (data.gallery_replacements?.[field] !== undefined) caption.append(element('small', 'gallery-pending', 'Pending store update'));
        figure.append(data[field] ? picture(data[field], title) : element('p', 'empty-shot', 'No photo'), caption);
        images.append(figure);
    }
    images.hidden = !images.childElementCount;
}

function renderJob(data, keepEdits = false) {
    const newJob = job?.job_id !== data.job_id;
    job = data;
    if (newJob) galleryMessage('');
    const {status, final_data: details} = data;
    const editable = ['PENDING_DETAILS', 'PENDING_REVIEW', 'APPROVED', 'PUBLISHED'].includes(status);
    find('#job-title').textContent = details.title || 'Product intake';
    find('#job-status').replaceWith(Object.assign(badge(status), {id: 'job-status'}));
    find('#job-instructions').textContent = stages[status] || '';
    renderProgress(data);
    find('#job-error').textContent = data.error_message;
    find('#job-error').hidden = !data.error_message;
    const extractionNotice = find('#extraction-notice');
    extractionNotice.textContent = details.extraction_notice || '';
    extractionNotice.hidden = status !== 'PENDING_DETAILS' || !details.extraction_notice;
    renderGallery(data);
    const references = find('#source-images');
    references.replaceChildren();
    const source = data.reference_photos || {};
    for (const [paths, title] of [[source.garments, 'Garment photo'], [source.model, 'Model reference'], [source.fabric ? [source.fabric] : [], 'Fabric reference']]) {
        for (const [index, url] of (paths || []).entries()) {
            const label = title + ' ' + (index + 1);
            const figure = element('figure');
            figure.append(picture(url, label), element('figcaption', '', label));
            references.append(figure);
        }
    }
    find('#reference-photos').hidden = !references.childElementCount;
    setupReviewAssets(data, details, source);

    productForm.hidden = !Object.keys(details).length;
    find('#quick-add-chart').disabled = !editable;
    if (!keepEdits) chartSelectionStatus.textContent = '';
    for (const field of Array.from(productForm.elements)) {
        if (!field.name) continue;
        if (keepEdits) {
            field.disabled = !editable || (field.name === 'generation_notes' && status !== 'PENDING_DETAILS');
            continue;
        }
        if (field.name === 'show_size_chart_in_description') field.checked = data.show_size_chart_in_description;
        else if (field.type === 'checkbox') field.checked = Boolean(details[field.name]);
        else if (field.name === 'size_chart_id') field.value = data.size_chart || '';
        else if (field.name === 'generation_notes') field.value = data.generation_notes || '';
        else if (field.name === 'attributes') {
            field.value = (details.attributes || []).map((attr) => {
                const options = Array.isArray(attr.options) ? attr.options.join(', ') : '';
                return attr.name ? attr.name + ': ' + options : '';
            }).filter(Boolean).join('\n');
        } else field.value = details[field.name] ?? '';
        field.disabled = !editable || (field.name === 'generation_notes' && status !== 'PENDING_DETAILS');
    }
    find('#generation-notes-label').hidden = status !== 'PENDING_DETAILS' && !data.generation_notes;
    previewChart();
    find('#save-details').hidden = !editable;
    find('#generate').hidden = status !== 'PENDING_DETAILS';
    find('#approve').hidden = status !== 'PENDING_REVIEW';
    find('#publish').hidden = !['PENDING_REVIEW', 'APPROVED'].includes(status);
    find('#update-listing').hidden = status !== 'PUBLISHED';
    find('#remove-deleted-listing').hidden = status !== 'PUBLISHED' || !data.woocommerce_product_id;
    find('#listing-dirty').hidden = !data.listing_dirty;
    find('#retry-images').hidden = !(status === 'FAILED' && Object.keys(details).length);
    find('#retry-extraction').hidden = !(status === 'FAILED' && !Object.keys(details).length);
    const link = find('#store-link');
    link.hidden = !/^https?:\/\//i.test(data.woocommerce_permalink);
    if (!link.hidden) link.href = data.woocommerce_permalink;

    clearTimeout(timer);
    if (['PROCESSING', 'PUBLISHING'].includes(status)) {
        timer = setTimeout(() => openJob(data.job_id, false), 1500);
    }
}

async function replacePhoto(id, field, input) {
    const image = input.files[0];
    if (!image) return;
    const form = new FormData();
    form.set('image', image);
    form.set('shot', field);
    input.disabled = true;
    try {
        const data = await request(jobPath(id) + 'replace_photo/', 'POST', form);
        if (tab !== 'review' || job?.job_id !== id) return;
        job = data;
        renderGallery(data);
        find('#listing-dirty').hidden = !data.listing_dirty;
        galleryMessage(data.status === 'PUBLISHED' ? 'Photo saved locally. Update the WooCommerce listing when ready.' : 'Uploaded photo saved.');
    } catch (error) {
        if (tab === 'review' && job?.job_id === id) galleryMessage(error.message, true);
    } finally {
        input.value = '';
        input.disabled = false;
    }
}

find('#remove-deleted-listing').onclick = async (event) => {
    if (!job || !window.confirm('Remove this dashboard entry if WooCommerce confirms the product was deleted?')) return;
    const id = job.job_id;
    const button = event.currentTarget;
    button.disabled = true;
    try {
        await request(jobPath(id) + 'remove_deleted_listing/', 'POST', {});
        if (job?.job_id === id) {
            job = null;
            changeTab('jobs');
            notice('The deleted listing was removed from the dashboard.');
        }
    } catch (error) {
        notice(error.message, true);
    } finally {
        button.disabled = false;
    }
};

async function regenerateShot(id, field) {
    if (regenerationJob === id) return;
    if (field === 'image_1_url' && !window.confirm('Regenerate Model front and Model side together? This uses up to two image requests.')) return;
    regenerationJob = id;
    const controls = [...find('#product-images').querySelectorAll('button')];
    controls.forEach((button) => { button.disabled = true; });
    galleryMessage(field === 'image_1_url' ? 'Regenerating the front and side photos…' : 'Regenerating ' + shots[field] + '…');
    try {
        const updated = await request(jobPath(id) + 'regenerate_shot/', 'POST', {shot: field});
        if (tab === 'review' && job?.job_id === id) renderJob(updated, true);
    } catch (error) {
        if (regenerationJob === id) regenerationJob = null;
        if (tab === 'review' && job?.job_id === id) galleryMessage(error.message, true);
    } finally {
        controls.forEach((button) => { button.disabled = false; });
    }
}

async function openJob(id, navigate = true) {
    const operation = ++reviewRequest;
    if (navigate) show('review');
    try {
        const data = await request(jobPath(id));
        if (operation !== reviewRequest || tab !== 'review') return;
        const regenerating = regenerationJob === id;
        renderJob(data, regenerating && !navigate);
        if (regenerating && data.status !== 'PROCESSING') {
            regenerationJob = null;
            galleryMessage(data.error_message || 'Regenerated photos are ready for review.', Boolean(data.error_message));
        } else if (regenerating) {
            galleryMessage(data.workflow_progress?.label || 'Regenerating photos…');
        }
    } catch (error) {
        if (operation !== reviewRequest || tab !== 'review') return;
        if (!navigate && (error instanceof TypeError || error.status >= 500)) {
            galleryMessage('Connection interrupted. Checking progress again…');
            timer = setTimeout(() => openJob(id, false), 2500);
        } else {
            if (regenerationJob === id) regenerationJob = null;
            notice(error.message, true);
        }
    }
}

find('#intake-form').onsubmit = async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    if (data.getAll('garment_images').length > 7) {
        notice('Choose up to seven garment photos.', true);
        return;
    }
    if (!data.get('model_profile_id')) data.delete('model_profile_id');
    if (!data.get('fabric_reference').size) data.delete('fabric_reference');
    event.submitter.disabled = true;
    try {
        const created = await request('jobs/create_job/', 'POST', data);
        form.reset();
        showIntakeProfile();
        show('review');
        renderJob(created);
    } catch (error) {
        notice(error.message, true);
    } finally {
        event.submitter.disabled = false;
    }
};

function reviewerEdits() {
    const edits = Object.fromEntries(new FormData(productForm));
    delete edits.size_chart_id;
    delete edits.show_size_chart_in_description;
    delete edits.generation_notes;
    edits.manage_stock = productForm.elements.manage_stock.checked;
    edits.stock_quantity = edits.stock_quantity === '' ? null : Number(edits.stock_quantity);
    edits.attributes = edits.attributes.split('\n').filter((line) => line.trim()).map((line) => {
        const colon = line.indexOf(':');
        const name = line.slice(0, colon).trim();
        const options = line.slice(colon + 1).split(',').map((value) => value.trim()).filter(Boolean);
        if (colon < 1 || !name) throw new Error('Enter attributes as Name: value, one attribute per line.');
        return {name, options, visible: true};
    });
    return edits;
}

function setReviewBusy(busy) {
    productForm.querySelectorAll('button[type=submit]').forEach((button) => { button.disabled = busy; });
    find('#quick-add-chart').disabled = busy || chartSelect.disabled;
}

productForm.onsubmit = async (event) => {
    event.preventDefault();
    const action = event.submitter.id;
    setReviewBusy(true);
    notice('');
    try {
        const path = jobPath(job.job_id);
        const chart = find('#size-chart').value;
        const edits = {
            reviewer_edits: reviewerEdits(),
            show_size_chart_in_description: productForm.elements.show_size_chart_in_description.checked,
        };
        if (job.status === 'PENDING_DETAILS') edits.generation_notes = productForm.elements.generation_notes.value;
        if (chart || !job.size_chart || charts.some((item) => item.id === job.size_chart)) {
            edits.size_chart_id = chart ? Number(chart) : null;
        }
        const saved = await request(path + 'edit_job/', 'POST', edits);
        find('#listing-dirty').hidden = !saved.listing_dirty;
        if (action === 'generate') renderJob(await request(path + 'generate/', 'POST', {
            generation_notes: productForm.elements.generation_notes.value,
        }));
        else if (['approve', 'publish', 'update-listing'].includes(action)) {
            const updating = action === 'update-listing';
            let publishing = action !== 'approve';
            const poll = async () => {
                if (!publishing || tab !== 'review') return;
                try {
                    const current = await request(path);
                    if (publishing && current.status === 'PUBLISHING') renderProgress(current);
                } catch {}
                if (publishing) setTimeout(poll, 1200);
            };
            if (publishing) setTimeout(poll, 750);
            try {
                renderJob(await request(path + (updating ? 'update_listing/' : 'approve_publish/'), 'POST',
                    updating ? {} : {publish_to_woocommerce: publishing}));
            } finally {
                publishing = false;
            }
            notice(updating ? 'WooCommerce listing updated.' : action === 'publish' ? 'Product published.' : 'Product approved.');
        } else {
            renderJob(saved);
            notice('Product details saved.');
        }
    } catch (error) {
        notice(error.message, true);
    } finally {
        setReviewBusy(false);
    }
};

find('#retry-images').onclick = async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    notice('');
    try {
        renderJob(await request(jobPath(job.job_id) + 'generate/', 'POST', {}));
    } catch (error) {
        notice(error.message, true);
    } finally {
        button.disabled = false;
    }
};

find('#retry-extraction').onclick = async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    notice('');
    try {
        renderJob(await request(jobPath(job.job_id) + 'retry_extraction/', 'POST', {}));
    } catch (error) {
        notice(error.message, true);
    } finally {
        button.disabled = false;
    }
};

find('#job-search').onsubmit = (event) => {
    event.preventDefault();
    jobPage = 1;
    loadJobs();
};
find('#job-prev').onclick = () => { jobPage--; loadJobs(); };
find('#job-next').onclick = () => { jobPage++; loadJobs(); };
find('#cancel-profile').onclick = () => editProfile(null);
chartSelect.onchange = () => {
    chartSelectionStatus.textContent = '';
    previewChart();
};
find('#chart-search').oninput = renderCharts;
find('#quick-add-chart').onclick = () => quickChartDialog.showModal();

function chartFormMessage(form, text, isError = false) {
    const message = form.querySelector('.chart-form-message');
    message.textContent = text;
    message.classList.toggle('error', isError);
    message.hidden = !text;
}

function setChartFormBusy(form, busy) {
    form.querySelectorAll('button').forEach((button) => { button.disabled = busy; });
}

async function readChartPhoto(form) {
    const file = form.querySelector('[data-chart-source]').files[0];
    if (!file) {
        chartFormMessage(form, 'Choose a chart photo first.', true);
        return;
    }
    const data = new FormData();
    data.set('image', file);
    setChartFormBusy(form, true);
    chartFormMessage(form, 'Reading photo…');
    try {
        const result = await request('size-charts/read_photo/', 'POST', data);
        form.elements.measurements.value = result.measurements;
        form.elements.image.value = '';
        form.elements.measurements.focus();
        chartFormMessage(form, 'Check the transcribed values and replace any ? before saving.');
    } catch (error) {
        chartFormMessage(form, error.message, true);
    } finally {
        setChartFormBusy(form, false);
    }
}

async function saveSizeChart(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const targetJob = quickChartDialog.contains(form) ? job?.job_id : null;
    const data = new FormData(form);
    if (!data.get('image').size) data.delete('image');
    if (!data.get('measurements').trim()) data.delete('measurements');
    setChartFormBusy(form, true);
    chartFormMessage(form, 'Saving size chart…');
    try {
        const chart = await request('size-charts/', 'POST', data);
        form.reset();
        charts = [...charts.filter((item) => item.id !== chart.id), chart].sort((a, b) => a.name.localeCompare(b.name));
        renderCharts();
        if (targetJob && tab === 'review' && job?.job_id === targetJob && quickChartDialog.open) {
            chartFormMessage(form, '');
            chartSelect.value = String(chart.id);
            previewChart();
            quickChartDialog.close();
            chartSelect.focus();
            chartSelectionStatus.textContent = 'Chart selected. Save the product to apply it.';
        } else {
            chartFormMessage(form, 'Size chart saved.');
        }
        await loadCharts();
    } catch (error) {
        chartFormMessage(form, error.message, true);
    } finally {
        setChartFormBusy(form, false);
    }
}

for (const form of document.querySelectorAll('.size-chart-form')) {
    const readButton = form.querySelector('[data-read-chart]');
    readButton.onclick = () => readChartPhoto(form);
    form.onsubmit = saveSizeChart;
}

const fabricForm = find('#fabric-form');
function moveCropMarker() {
    const marker = find('#crop-marker');
    marker.style.left = fabricForm.elements.center_x.value + '%';
    marker.style.top = fabricForm.elements.center_y.value + '%';
}
fabricForm.oninput = moveCropMarker;
find('#crop-source').onclick = (event) => {
    const box = event.currentTarget.getBoundingClientRect();
    fabricForm.elements.center_x.value = Math.round((event.clientX - box.left) / box.width * 100);
    fabricForm.elements.center_y.value = Math.round((event.clientY - box.top) / box.height * 100);
    moveCropMarker();
};
fabricForm.onsubmit = async (event) => {
    event.preventDefault();
    const values = new FormData(fabricForm);
    event.submitter.disabled = true;
    try {
        const data = await request(jobPath(job.job_id) + 'crop_fabric/', 'POST', {
            center_x: Number(values.get('center_x')), center_y: Number(values.get('center_y')),
        });
        job = data;
        renderGallery(data);
        find('#listing-dirty').hidden = !data.listing_dirty;
        notice(data.status === 'PUBLISHED' ? 'Fabric crop saved locally. Update the WooCommerce listing when ready.' : 'Fabric crop saved.');
    } catch (error) {
        notice(error.message, true);
    } finally {
        event.submitter.disabled = false;
    }
};

find('#social-form').onsubmit = async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const id = job.job_id;
    const data = new FormData(form);
    if (!data.get('logo').size) data.delete('logo');
    event.submitter.disabled = true;
    try {
        const response = await fetch(api + jobPath(id) + 'export_social/', {
            method: 'POST', headers: {'X-CSRFToken': csrf}, credentials: 'same-origin', body: data,
        });
        if (!response.ok) {
            const error = await response.json();
            throw new Error(Object.values(error).flat().join(' '));
        }
        const url = URL.createObjectURL(await response.blob());
        const link = element('a');
        link.href = url;
        link.download = response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || 'social-campaign.zip';
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        form.elements.logo.value = '';
        find('#logo-help').textContent = 'Saved logo will be reused. Choose a PNG to replace it.';
        if (job.job_id === id) {
            job.social_draft = Object.fromEntries(['facebook', 'instagram', 'tiktok'].map((channel) => [channel, data.get(channel)]));
            job.social_logo_configured = true;
            renderProgress(job);
        }
        notice('Campaign ready to download.');
    } catch (error) {
        notice(error.message, true);
    } finally {
        event.submitter.disabled = false;
    }
};

find('#refresh-captions').onclick = () => {
    const form = find('#social-form');
    for (const channel of ['facebook', 'instagram', 'tiktok']) {
        form.elements[channel].value = job.suggested_captions?.[channel] || '';
    }
    notice('Suggestions refreshed from the saved product details.');
};

const campaignDialog = find('#campaign-confirmation');
const campaignForm = find('#campaign-confirm-form');
const socialLabels = {facebook: 'Facebook', instagram: 'Instagram', tiktok: 'TikTok'};
let campaign = null;

function renderSocialStates(data) {
    const publications = data.social_publications || {};
    for (const [platform, label] of Object.entries(socialLabels)) {
        const publication = publications[platform];
        const state = find('#social-' + platform);
        const button = find('[data-publish-channel="' + platform + '"]');
        state.hidden = !publication;
        state.textContent = publication?.message || (publication ? 'Sending to ' + label + '…' : '');
        state.classList.toggle('error', ['failed', 'blocked', 'unknown'].includes(publication?.state));
        button.disabled = Boolean(publication && publication.state !== 'failed');
    }
    find('#refresh-tiktok').hidden = !['processing', 'draft'].includes(publications.tiktok?.state);
}

function showPublication(id, publication) {
    if (job?.job_id !== id) return;
    job.social_publications = {...job.social_publications, [publication.platform]: publication};
    renderSocialStates(job);
}

async function confirmCampaign(platform) {
    const draft = find('#social-form');
    const caption = draft.elements[platform];
    if (!caption.reportValidity() || !job) return;
    const pending = {id: job.job_id, platform, caption: caption.value, logo: draft.elements.logo.files[0]};
    campaign = pending;
    campaignForm.reset();
    const tiktok = platform === 'tiktok';
    const button = find('#confirm-campaign');
    find('#campaign-confirm-title').textContent = tiktok ? 'Send photos to TikTok' : 'Publish to ' + socialLabels[platform];
    find('#campaign-account').textContent = tiktok
        ? 'Checking the TikTok account…'
        : 'These photos will be posted to your configured ' + socialLabels[platform] + ' account.';
    find('#campaign-caption').textContent = pending.caption;
    const preview = find('#campaign-preview');
    preview.replaceChildren();
    for (const [field, label] of Object.entries(shots)) {
        if (!job[field]) continue;
        const photo = element('img');
        photo.src = job[field];
        photo.alt = label;
        preview.append(photo);
    }
    find('#campaign-title-field').hidden = !tiktok;
    campaignForm.elements.title.required = tiktok;
    if (tiktok) campaignForm.elements.title.value = (job.final_data?.title || 'Product photos').slice(0, 90);
    button.textContent = tiktok ? 'Send to TikTok inbox' : 'Publish now';
    button.disabled = tiktok;
    campaignDialog.showModal();
    if (!tiktok) return;
    try {
        const account = await request('social/tiktok/');
        if (campaign !== pending) return;
        find('#campaign-account').textContent = account.ok
            ? 'Send to ' + account.account + '. Open the TikTok inbox to review and publish your draft.'
            : account.message;
        button.disabled = !account.ok;
    } catch (error) {
        if (campaign === pending) find('#campaign-account').textContent = error.message;
    }
}

document.querySelectorAll('[data-publish-channel]').forEach((button) => {
    button.onclick = () => confirmCampaign(button.dataset.publishChannel);
});
find('#cancel-campaign').onclick = () => campaignDialog.close();
campaignDialog.onclose = () => { campaign = null; };
campaignDialog.addEventListener('cancel', (event) => {
    if (campaign?.sending) event.preventDefault();
});

campaignForm.onsubmit = async (event) => {
    event.preventDefault();
    if (!campaign || campaign.sending || find('#confirm-campaign').disabled) return;
    const {id, platform, caption, logo} = campaign;
    const cancel = find('#cancel-campaign');
    const submit = event.submitter;
    const data = new FormData(campaignForm);
    data.set('platform', platform);
    data.set('caption', caption);
    data.set('consent', 'true');
    if (platform !== 'tiktok') {
        data.delete('title');
        if (logo) data.set('logo', logo);
    }
    campaign.sending = true;
    submit.disabled = cancel.disabled = true;
    try {
        const publication = await request(jobPath(id) + 'publish_social/', 'POST', data);
        showPublication(id, publication);
        if (job?.job_id === id && ['published', 'processing'].includes(publication.state)) {
            job.social_draft = {...job.social_draft, [platform]: caption};
            if (platform !== 'tiktok' && logo) {
                job.social_logo_configured = true;
                find('#logo-help').textContent = 'Saved logo will be reused. Choose a PNG to replace it.';
                find('#social-form').elements.logo.value = '';
            }
        }
        campaignDialog.close();
    } catch (error) {
        find('#campaign-account').textContent = error.message;
    } finally {
        if (campaign) campaign.sending = false;
        submit.disabled = cancel.disabled = false;
    }
};

find('#refresh-tiktok').onclick = async (event) => {
    const button = event.currentTarget;
    const id = job.job_id;
    button.disabled = true;
    try {
        showPublication(id, await request(jobPath(id) + 'refresh_social/', 'POST', {}));
    } catch (error) {
        notice(error.message, true);
    } finally {
        button.disabled = false;
    }
};

find('#test-social').onclick = async (event) => {
    const button = event.currentTarget;
    const results = find('#social-results');
    button.disabled = true;
    results.textContent = 'Checking social accounts…';
    try {
        const data = await request('social/connections/', 'POST', {});
        results.replaceChildren();
        for (const [key, label] of [['facebook', 'Facebook'], ['instagram', 'Instagram'], ['tiktok', 'TikTok']]) {
            const entry = data[key];
            const line = element('p', entry.ok === null ? '' : entry.ok ? 'notice' : 'notice error');
            line.append(element('strong', '', label + ' '), document.createTextNode(entry.message));
            results.append(line);
        }
    } catch (error) {
        results.replaceChildren(element('p', 'notice error', error.message));
    } finally {
        button.disabled = false;
    }
};
find('#test-store').onclick = async (event) => {
    const button = event.currentTarget;
    const results = find('#store-results');
    button.disabled = true;
    results.textContent = 'Checking store access…';
    try {
        const data = await request('store/connection/', 'POST', {});
        results.replaceChildren();
        for (const [name, entry] of [['WooCommerce', data.woocommerce], ['Media', data.media]]) {
            const line = element('p', entry.ok === null ? '' : entry.ok ? 'notice' : 'notice error');
            line.append(element('strong', '', name + ' '), document.createTextNode(entry.message));
            results.append(line);
        }
    } catch (error) {
        results.replaceChildren(element('p', 'notice error', error.message));
    } finally {
        button.disabled = false;
    }
};

function updateImageProviderControls() {
    const select = find('#image-provider-select');
    const model = find('#image-provider-model');
    model.hidden = !providerSettings;
    if (providerSettings) {
        const activeModel = select.value === 'gemini' ? providerSettings.gemini_model : providerSettings.openrouter_model;
        model.textContent = activeModel ? 'Image model ' + activeModel : '';
    }
    find('#save-image-provider').disabled = select.disabled || !providerSettings
        || select.value === providerSettings.provider
        || (select.value === 'gemini' && !providerSettings.gemini_configured);
}

function renderImageProvider(data) {
    providerSettings = data;
    const select = find('#image-provider-select');
    select.options.namedItem('gemini-option').disabled = !data.gemini_configured;
    select.value = data.provider;
    select.disabled = false;
    const provider = data.provider === 'gemini' ? 'Gemini' : 'OpenRouter';
    find('#image-provider-status').textContent = data.gemini_configured
        ? provider + ' is active for image generation.'
        : provider + ' is active. Add GEMINI_API_KEY to .env and restart to enable Gemini.';
    const model = data.provider === 'gemini' ? data.gemini_model : data.openrouter_model;
    find('#on-model-provider').textContent = provider + (model ? ' · ' + model : '');
    find('#flat-lay-provider').textContent = model || provider;
    updateImageProviderControls();
}

async function loadImageProvider() {
    const current = ++providerRequest;
    const select = find('#image-provider-select');
    select.disabled = true;
    find('#image-provider-status').textContent = 'Loading provider settings…';
    find('#return-to-product').hidden = !job;
    updateImageProviderControls();
    try {
        const data = await request('image-provider/');
        if (tab === 'image-settings' && current === providerRequest) renderImageProvider(data);
    } catch (error) {
        if (tab === 'image-settings' && current === providerRequest) find('#image-provider-status').textContent = error.message;
    }
}

find('#image-provider-select').onchange = updateImageProviderControls;
find('#return-to-product').onclick = () => { if (job) openJob(job.job_id); };
find('#image-provider-form').onsubmit = async (event) => {
    event.preventDefault();
    const select = find('#image-provider-select');
    if (find('#save-image-provider').disabled) return;
    const current = ++providerRequest;
    const provider = select.value;
    select.disabled = true;
    updateImageProviderControls();
    try {
        const saved = await request('image-provider/', 'POST', {provider});
        if (tab === 'image-settings' && current === providerRequest) {
            renderImageProvider(saved);
            notice('Image provider saved.');
        }
    } catch (error) {
        if (tab === 'image-settings' && current === providerRequest) {
            select.disabled = false;
            updateImageProviderControls();
            notice(error.message, true);
        }
    }
};
profileSelect.onchange = showIntakeProfile;
document.querySelectorAll('[data-tab]').forEach((button) => {
    button.onclick = () => changeTab(button.dataset.tab);
});
changeTab('intake');
