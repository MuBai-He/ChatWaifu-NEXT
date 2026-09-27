#import <Foundation/Foundation.h>
#import <EventKit/EventKit.h>
#import <AppKit/AppKit.h>

static NSDictionary *failure(NSString *code) { return @{ @"error": code }; }
static NSDate *dateValue(id value) {
    if (![value isKindOfClass:NSString.class]) return nil;
    NSISO8601DateFormatter *f = [NSISO8601DateFormatter new];
    NSDate *d = [f dateFromString:value];
    if (!d) { f.formatOptions |= NSISO8601DateFormatWithFractionalSeconds; d = [f dateFromString:value]; }
    return d;
}
static NSDictionary *itemValue(EKCalendarItem *item) {
    NSMutableDictionary *v = [@{@"id": item.calendarItemIdentifier ?: @"", @"title": item.title ?: @"", @"modified": @(item.lastModifiedDate.timeIntervalSince1970), @"calendar_id": item.calendar.calendarIdentifier ?: @""} mutableCopy];
    if ([item isKindOfClass:EKEvent.class]) {
        EKEvent *e = (EKEvent *)item;
        v[@"start"] = @(e.startDate.timeIntervalSince1970); v[@"end"] = @(e.endDate.timeIntervalSince1970);
        v[@"recurring"] = @(e.hasRecurrenceRules); v[@"all_day"] = @(e.allDay);
    } else {
        EKReminder *r = (EKReminder *)item;
        v[@"completed"] = @(r.completed);
        if (r.dueDateComponents) {
            NSCalendar *cal = [NSCalendar calendarWithIdentifier:NSCalendarIdentifierGregorian];
            cal.timeZone = r.dueDateComponents.timeZone ?: NSTimeZone.localTimeZone;
            NSDate *due = [cal dateFromComponents:r.dueDateComponents];
            if (due) v[@"due"] = @(due.timeIntervalSince1970);
        }
    }
    return v;
}
static NSArray<EKReminder *> *reminders(EKEventStore *store, EKCalendar *calendar) {
    dispatch_semaphore_t signal = dispatch_semaphore_create(0);
    __block NSArray *items = nil;
    id token = [store fetchRemindersMatchingPredicate:[store predicateForRemindersInCalendars:@[calendar]] completion:^(NSArray *result) { items = result; dispatch_semaphore_signal(signal); }];
    if (dispatch_semaphore_wait(signal, dispatch_time(DISPATCH_TIME_NOW, 15 * NSEC_PER_SEC)) != 0) {
        [store cancelFetchRequest:token]; return nil;
    }
    return items;
}
static NSDictionary *execute(NSDictionary *request) {
    NSString *action = request[@"action"];
    if ([action isEqualToString:@"sound"]) {
        dispatch_async(dispatch_get_main_queue(), ^{ [[NSSound soundNamed:@"Glass"] play]; });
        return @{ @"ok": @YES };
    }
    NSString *resource = request[@"resource"];
    EKEntityType type = [resource isEqualToString:@"reminder"] ? EKEntityTypeReminder : EKEntityTypeEvent;
    EKEventStore *store = [EKEventStore new];
    if ([action isEqualToString:@"permission"]) {
        dispatch_semaphore_t signal = dispatch_semaphore_create(0);
        __block BOOL granted = NO;
        void (^callback)(BOOL, NSError *) = ^(BOOL ok, NSError *__unused error) { granted = ok; dispatch_semaphore_signal(signal); };
        if (type == EKEntityTypeEvent) [store requestFullAccessToEventsWithCompletion:callback];
        else [store requestFullAccessToRemindersWithCompletion:callback];
        if (dispatch_semaphore_wait(signal, dispatch_time(DISPATCH_TIME_NOW, 120 * NSEC_PER_SEC)) != 0) return failure(@"permission_request_timed_out");
        return @{ @"granted": @(granted) };
    }
    EKAuthorizationStatus status = [EKEventStore authorizationStatusForEntityType:type];
    if (status != EKAuthorizationStatusFullAccess) return failure(@"apple_permission_required");
    if ([action isEqualToString:@"sources"]) {
        NSMutableArray *items = [NSMutableArray new];
        for (EKCalendar *calendar in [store calendarsForEntityType:type]) {
            if (items.count == 50) break;
            [items addObject:@{ @"id": calendar.calendarIdentifier, @"title": calendar.title ?: @"", @"resource": resource, @"writable": @(calendar.allowsContentModifications) }];
        }
        return @{ @"items": items };
    }
    EKCalendar *calendar = [store calendarWithIdentifier:request[@"calendar_id"]];
    if (!calendar || (type == EKEntityTypeEvent ? !(calendar.allowedEntityTypes & EKEntityMaskEvent) : !(calendar.allowedEntityTypes & EKEntityMaskReminder))) return failure(@"source_unavailable");
    BOOL reading = [action isEqualToString:@"list"];
    if (!reading && !calendar.allowsContentModifications) return failure(@"source_readonly");
    NSArray *items = nil;
    if (type == EKEntityTypeReminder) {
        items = reminders(store, calendar);
        if (!items) return failure(@"apple_query_timeout");
    } else if (reading || [action isEqualToString:@"create"]) {
        NSDate *start = dateValue(request[@"start"]), *end = dateValue(request[@"end"]);
        if (!start || !end || [end timeIntervalSinceDate:start] <= 0 || [end timeIntervalSinceDate:start] > 31 * 86400) return failure(@"invalid_time_range");
        items = [store eventsMatchingPredicate:[store predicateForEventsWithStartDate:start endDate:end calendars:@[calendar]]];
    }
    if (reading) {
        NSMutableArray *result = [NSMutableArray new];
        for (EKCalendarItem *item in items) { if (result.count == 200) break; [result addObject:itemValue(item)]; }
        return @{ @"items": result, @"truncated": @(items.count > 200), @"source": @"eventkit_local" };
    }
    EKCalendarItem *item;
    BOOL creating = [action isEqualToString:@"create"];
    NSURL *marker = [NSURL URLWithString:[@"chatwaifu://operation/" stringByAppendingString:request[@"request_id"] ?: @""]];
    if (creating) {
        for (EKCalendarItem *old in items) { if ([old.URL isEqual:marker]) return @{ @"item": itemValue(old), @"source": @"eventkit_local" }; }
        item = type == EKEntityTypeEvent ? [EKEvent eventWithEventStore:store] : [EKReminder reminderWithEventStore:store];
        item.calendar = calendar; item.URL = marker;
    } else {
        item = [store calendarItemWithIdentifier:request[@"item_id"]];
        if (!item || ![item.calendar.calendarIdentifier isEqualToString:calendar.calendarIdentifier]) return failure(@"item_not_found_in_selected_source");
        if (fabs(item.lastModifiedDate.timeIntervalSince1970 - [request[@"expected_modified"] doubleValue]) > 0.001) return failure(@"item_changed_refresh_before_editing");
        if (item.hasRecurrenceRules) return failure(@"recurring_item_edits_unsupported");
        if ([item isKindOfClass:EKEvent.class] && (((EKEvent *)item).hasAttendees || ((EKEvent *)item).allDay)) return failure(@"invited_or_all_day_event_edits_unsupported");
    }
    NSError *error = nil;
    BOOL saved = NO;
    if ([action isEqualToString:@"delete"]) {
        saved = type == EKEntityTypeEvent ? [store removeEvent:(EKEvent *)item span:EKSpanThisEvent commit:YES error:&error] : [store removeReminder:(EKReminder *)item commit:YES error:&error];
        return saved ? @{ @"deleted": @YES, @"source": @"eventkit_local" } : failure(@"apple_save_failed");
    }
    if (![action isEqualToString:@"complete"]) {
        item.title = request[@"title"];
        if (type == EKEntityTypeEvent) {
            EKEvent *event = (EKEvent *)item;
            event.startDate = dateValue(request[@"start"]); event.endDate = dateValue(request[@"end"]);
            if (!event.startDate || !event.endDate || [event.endDate timeIntervalSinceDate:event.startDate] <= 0) return failure(@"invalid_time_range");
        } else if (dateValue(request[@"start"])) {
            NSCalendar *cal = [NSCalendar calendarWithIdentifier:NSCalendarIdentifierGregorian];
            cal.timeZone = [NSTimeZone timeZoneForSecondsFromGMT:0];
            NSDateComponents *components = [cal components:NSCalendarUnitYear|NSCalendarUnitMonth|NSCalendarUnitDay|NSCalendarUnitHour|NSCalendarUnitMinute fromDate:dateValue(request[@"start"])];
            components.timeZone = cal.timeZone;
            ((EKReminder *)item).dueDateComponents = components;
        }
    } else if (type == EKEntityTypeReminder) { ((EKReminder *)item).completed = YES; }
    else return failure(@"unsupported_action");
    saved = type == EKEntityTypeEvent ? [store saveEvent:(EKEvent *)item span:EKSpanThisEvent commit:YES error:&error] : [store saveReminder:(EKReminder *)item commit:YES error:&error];
    if (!saved) return failure(@"apple_save_failed");
    EKCalendarItem *readback = [store calendarItemWithIdentifier:item.calendarItemIdentifier];
    return readback ? @{ @"item": itemValue(readback), @"source": @"eventkit_local" } : failure(@"apple_saved_readback_unavailable");
}
char *cw_apple(const char *input) {
    @autoreleasepool {
        NSDictionary *result;
        @try {
            NSData *data = [[NSString stringWithUTF8String:input] dataUsingEncoding:NSUTF8StringEncoding];
            NSDictionary *request = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
            result = execute(request);
        } @catch (NSException *exception) { result = failure(@"apple_native_error"); }
        NSData *data = [NSJSONSerialization dataWithJSONObject:result options:0 error:nil];
        return strdup([[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding].UTF8String);
    }
}
